"""Moonraker integration for Home Assistant."""

import asyncio
import logging
import os.path
import uuid
from datetime import timedelta
from typing import Any

import async_timeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import MoonrakerApiClient
from .const import (
    CONF_API_KEY,
    CONF_PORT,
    CONF_PRINTER_NAME,
    CONF_OPTION_POLLING_RATE,
    CONF_TLS,
    CONF_URL,
    DOMAIN,
    FAST_POLL_SECONDS,
    HOSTNAME,
    INTERVAL_HYSTERESIS_SECONDS,
    METHODS,
    OBJ,
    PLATFORMS,
    SLOW_POLL_DEFAULT_SECONDS,
    TIMEOUT,
    PRINTSTATES,
)
from .sensor import SENSORS

_LOGGER = logging.getLogger(__name__)


async def async_setup(_hass: HomeAssistant, _config: ConfigType):
    """Set up this integration using YAML is not supported."""
    return True


def get_user_name(hass: HomeAssistant, entry: ConfigEntry):
    """Get username."""
    device_registry = dr.async_get(hass)
    device_entries = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    if len(device_entries) < 1:
        return None
    return device_entries[0].name_by_user


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""
    hass.data.setdefault(DOMAIN, {})

    custom_name = get_user_name(hass, entry)

    url = entry.data.get(CONF_URL)
    port = entry.data.get(CONF_PORT)
    tls = entry.data.get(CONF_TLS)
    api_key = entry.data.get(CONF_API_KEY)
    printer_name = entry.data.get(CONF_PRINTER_NAME) if custom_name is None else custom_name

    api = MoonrakerApiClient(
        url,
        async_get_clientsession(hass, verify_ssl=False),
        port=port,
        api_key=api_key,
        tls=tls,
    )

    # Try to reach the printer. If it's offline we still load the entry so
    # entities stay registered (just unavailable) until it comes back, rather
    # than putting the integration in "Failed setup, will retry".
    api_device_name: str | None = None
    try:
        async with async_timeout.timeout(TIMEOUT):
            await api.start()
            printer_info = await api.client.call_method("printer.info")
            _LOGGER.debug(printer_info)
            api_device_name = (
                printer_name if printer_name else printer_info.get(HOSTNAME)
            )
    except Exception as exc:
        _LOGGER.warning(
            "Moonraker at %s:%s unreachable during setup (%s); "
            "loading entry anyway, will reconnect in the background.",
            url,
            port,
            exc,
        )

    if not api_device_name:
        # Fall back to whatever we already had: a user-chosen name, the
        # previous title from a successful setup, or finally the URL.
        api_device_name = printer_name or entry.title or url

    if entry.title != api_device_name:
        hass.config_entries.async_update_entry(entry, title=api_device_name)

    coordinator = MoonrakerDataUpdateCoordinator(
        hass, client=api, config_entry=entry, api_device_name=api_device_name
    )

    # Best-effort first refresh; failure here is OK — the coordinator will
    # keep retrying and entities will simply be marked unavailable.
    await coordinator.async_refresh()
    # Ensure platform helpers that scribble into coordinator.data don't trip
    # on None when the very first refresh failed (printer offline at boot).
    if coordinator.data is None:
        coordinator.data = {}

    hass.data[DOMAIN][entry.entry_id] = coordinator
    for platform in PLATFORMS:
        coordinator.platforms.append(platform)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def send_gcode_service(service_call):
        """Handle the service call to send g-code."""
        gcode = service_call.data["gcode"]
        device_id = service_call.data["device_id"][0]
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(device_id)
        entry_id = getattr(device, "primary_config_entry", None)
        if entry_id is None or entry_id not in hass.data.get(DOMAIN, {}):
            entry_id = next(
                (eid for eid in device.config_entries if eid in hass.data.get(DOMAIN, {})),
                None,
            )
        if entry_id is None:
            _LOGGER.warning("send_gcode: no Moonraker entry found for device %s", device_id)
            return
        await hass.data[DOMAIN][entry_id].async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": gcode},
        )

    if not hass.services.has_service(DOMAIN, "send_gcode"):
        hass.services.async_register(DOMAIN, "send_gcode", send_gcode_service)
    return True


async def _printer_objects_updater(coordinator):
    return await coordinator._async_fetch_data(
        METHODS.PRINTER_OBJECTS_QUERY, coordinator.query_obj
    )


async def _printer_info_updater(coordinator):
    return {"printer.info": await coordinator._async_fetch_data(METHODS.PRINTER_INFO, None)}


async def _gcode_file_detail_updater(coordinator):
    data = await coordinator._async_fetch_data(
        METHODS.PRINTER_OBJECTS_QUERY, coordinator.query_obj
    )
    filename = ""
    if "status" in data:
        filename = data["status"]["print_stats"]["filename"]
    return await coordinator._async_get_gcode_file_detail(filename)


class MoonrakerDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MoonrakerApiClient,
        config_entry: ConfigEntry,
        api_device_name: str,
    ) -> None:
        """Initialize."""
        self.moonraker = client
        self.platforms = []
        self.updaters = [
            _printer_objects_updater,
            _printer_info_updater,
            _gcode_file_detail_updater,
        ]
        self.hass = hass
        self.config_entry = config_entry
        self.api_device_name = api_device_name
        self.query_obj = {OBJ: {}}
        self.load_sensor_data(SENSORS)

        self._last_interval_change = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._slow_seconds()),
        )

    def _slow_seconds(self) -> int:
        """Return configured slow-poll cadence (seconds)."""
        try:
            return int(
                self.config_entry.options.get(
                    CONF_OPTION_POLLING_RATE, SLOW_POLL_DEFAULT_SECONDS
                )
            )
        except (TypeError, ValueError):
            return SLOW_POLL_DEFAULT_SECONDS

    async def _async_update_data(self):
        """Update data via library."""
        data = {}
        for updater in self.updaters:
            data.update(await updater(self))

        # Adaptive polling (fast while printing, slow otherwise) with hysteresis
        self._apply_adaptive_interval(data)

        return data

    def _apply_adaptive_interval(self, fresh_data: dict) -> None:
        """Fast while printing, slow otherwise, with hysteresis."""
        slow_seconds = self._slow_seconds()
        current_state = (
            fresh_data.get("status", {}).get("print_stats", {}).get("state")
        )
        target_seconds = (
            FAST_POLL_SECONDS
            if current_state == PRINTSTATES.PRINTING.value
            else slow_seconds
        )
        target_td = timedelta(seconds=target_seconds)

        if self.update_interval == target_td:
            return

        now = dt_util.utcnow()
        if (
            self._last_interval_change is not None
            and (now - self._last_interval_change).total_seconds()
            < INTERVAL_HYSTERESIS_SECONDS
        ):
            return

        _LOGGER.debug(
            "Adjusting polling interval to %ss (state=%s)", target_seconds, current_state
        )
        self.update_interval = target_td
        self._last_interval_change = now
        self._schedule_refresh()

    async def _async_get_gcode_file_detail(self, gcode_filename):
        return_gcode = {
            "thumbnails_path": None,
            "estimated_time": 1,
            "filament_total": 1,
            "layer_count": None,
            "layer_height": None,
            "object_height": None,
            "first_layer_height": None,
        }
        if gcode_filename is None or gcode_filename == "":
            return return_gcode

        # Get prefix of the filename to get the appropriate thumbnail
        dirname = os.path.dirname(gcode_filename)

        query_object = {"filename": gcode_filename}
        gcode = await self._async_fetch_data(METHODS.SERVER_FILES_METADATA, query_object)
        return_gcode["estimated_time"] = gcode.get("estimated_time", 0)
        return_gcode["object_height"] = gcode.get("object_height", 0)
        return_gcode["filament_total"] = gcode.get("filament_total", 0)
        return_gcode["layer_count"] = gcode.get("layer_count", 0)
        return_gcode["layer_height"] = gcode.get("layer_height", 0)
        return_gcode["first_layer_height"] = gcode.get("first_layer_height", 0)

        try:
            thumbnails = gcode.get("thumbnails") or []
            if thumbnails:
                biggest = max(thumbnails, key=lambda t: t.get("size", 0))
                return_gcode["thumbnails_path"] = os.path.join(
                    dirname, biggest["relative_path"]
                )
        except Exception as ex:
            _LOGGER.warning("failed to get thumbnails: %s", ex)
            _LOGGER.debug("Query Object: %s", query_object)
            _LOGGER.debug("gcode: %s", gcode)
        return return_gcode

    async def _async_fetch_data(self, query_path: METHODS, query_object, quiet: bool = False):
        myuuid = str(uuid.uuid4())
        _LOGGER.debug("fetching data, uuid: %s, from: %s", myuuid, query_path.value)
        _LOGGER.debug("fetching, uuid: %s, object: %s", myuuid, query_object)
        try:
            if not self.moonraker.client.is_connected:
                _LOGGER.debug("connection to moonraker down, restarting")
                await self.moonraker.start()
            if query_object is None:
                result = await self.moonraker.client.call_method(query_path.value)
            else:
                result = await self.moonraker.client.call_method(query_path.value, **query_object)
            if not quiet:
                _LOGGER.debug("Query Result, uuid: %s: %s", myuuid, result)
            return result
        except Exception as exception:
            raise UpdateFailed() from exception

    async def _async_send_data(self, query_path: METHODS, query_obj) -> None:
        try:
            if not self.moonraker.client.is_connected:
                _LOGGER.debug("connection to moonraker down, restarting")
                await self.moonraker.start()
            if query_obj is None:
                await self.moonraker.client.call_method(query_path.value)
            else:
                await self.moonraker.client.call_method(query_path.value, **query_obj)
        except Exception as exception:
            raise UpdateFailed() from exception

    async def async_fetch_data(
        self,
        query_path: METHODS,
        query_obj: dict[str, Any] | None = None,
        quiet: bool = False,
    ):
        """Fetch data from moonraker."""
        return await self._async_fetch_data(query_path, query_obj, quiet=quiet)

    async def async_send_data(
        self, query_path: METHODS, query_obj: dict[str, Any] | None = None
    ):
        """Send data to moonraker."""
        return await self._async_send_data(query_path, query_obj)

    def add_data_updater(self, updater):
        """Update the data."""
        self.updaters.append(updater)

    def load_sensor_data(self, sensor_list):
        """Load sensor data, so we can poll the right object."""
        for sensor in sensor_list:
            if not getattr(sensor, "subscriptions", None):
                continue
            for subscriptions in sensor.subscriptions:
                if not isinstance(subscriptions, tuple) or len(subscriptions) < 2:
                    continue
                self.add_query_objects(subscriptions[0], subscriptions[1])

    def add_query_objects(self, query_object: str, result_key: str):
        """Build the list of object we want to retrieve from the server."""
        if query_object not in self.query_obj[OBJ]:
            self.query_obj[OBJ][query_object] = []
        if result_key not in self.query_obj[OBJ][query_object]:
            self.query_obj[OBJ][query_object].append(result_key)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    unloaded = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
                if platform in coordinator.platforms
            ]
        )
    )
    if unloaded:
        try:
            await coordinator.moonraker.stop()
        except Exception as exc:
            _LOGGER.debug("Error stopping Moonraker client on unload: %s", exc)
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "send_gcode")
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    hass.data[DOMAIN][entry.entry_id].config_entry = entry
    await hass.config_entries.async_reload(entry.entry_id)
