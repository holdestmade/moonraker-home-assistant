"""Switch platform for Moonraker integration."""
import logging
from dataclasses import dataclass
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN, METHODS
from .entity import BaseMoonrakerEntity
from .helpers import get_config_settings, get_object_list, is_output_pin

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MoonrakerSwitchSensorDescription(SwitchEntityDescription):
    """Class describing Moonraker switch entities."""

    sensor_name: Optional[str] = None
    icon: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the switch platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    builders: list = []
    await _collect_power_devices(coordinator, builders)
    await _collect_output_pin_switches(coordinator, builders)

    if not builders:
        return
    await coordinator.async_refresh()
    async_add_devices(b(coordinator, entry) for b in builders)


async def _power_device_updater(coordinator, _data):
    return {
        "power_devices": await coordinator.async_fetch_data(
            METHODS.MACHINE_DEVICE_POWER_DEVICES
        )
    }


async def _collect_output_pin_switches(coordinator, builders):
    """Collect digital (non-PWM) output_pin switches."""
    object_list = await get_object_list(coordinator)
    settings = await get_config_settings(coordinator)

    new_descs: list[MoonrakerSwitchSensorDescription] = []
    for obj in object_list.get("objects", []):
        if not is_output_pin(obj):
            continue

        conf = (
            settings.get("status", {})
            .get("configfile", {})
            .get("settings", {})
            .get(obj.lower(), {})
        )
        if conf.get("pwm", False):
            continue

        new_descs.append(
            MoonrakerSwitchSensorDescription(
                key=obj,
                sensor_name=obj,
                name=obj.replace("_", " ").title(),
                icon="mdi:switch",
                subscriptions=[(obj, "value")],
            )
        )

    if new_descs:
        coordinator.load_sensor_data(new_descs)
        for desc in new_descs:
            builders.append(
                lambda coord, ent, d=desc: MoonrakerDigitalOutputPin(coord, ent, d)
            )


async def _collect_power_devices(coordinator, builders):
    """Collect Moonraker [power] device switches."""
    try:
        power_devices = await coordinator.async_fetch_data(
            METHODS.MACHINE_DEVICE_POWER_DEVICES
        )
    except UpdateFailed as exc:
        _LOGGER.debug("Skipping power device discovery: %s", exc)
        return
    if power_devices.get("error"):
        return

    coordinator.add_data_updater(_power_device_updater)

    new_descs: list[MoonrakerSwitchSensorDescription] = []
    for device in power_devices["devices"]:
        new_descs.append(
            MoonrakerSwitchSensorDescription(
                key=device["device"],
                sensor_name=device["device"],
                name=device["device"].replace("_", " ").title(),
                icon="mdi:power",
                subscriptions=[],
            )
        )

    if new_descs:
        coordinator.load_sensor_data(new_descs)
        for desc in new_descs:
            builders.append(
                lambda coord, ent, d=desc: MoonrakerPowerDeviceSwitchSensor(coord, ent, d)
            )


class MoonrakerSwitchSensor(BaseMoonrakerEntity, SwitchEntity):
    """Moonraker switch class."""

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self.sensor_name = description.sensor_name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_icon = description.icon


class MoonrakerPowerDeviceSwitchSensor(MoonrakerSwitchSensor):
    """Moonraker power device switch class."""

    @property
    def is_on(self) -> bool:
        for device in self.coordinator.data.get("power_devices", {}).get("devices", []):
            if device["device"] == self.sensor_name:
                return device["status"] == "on"
        return False

    async def async_turn_on(self, **_: Any) -> None:
        await self.coordinator.async_send_data(
            METHODS.MACHINE_DEVICE_POWER_POST_DEVICE,
            {"device": self.sensor_name, "action": "on"},
        )
        # optimistic: flip local cache if present
        for device in self.coordinator.data.get("power_devices", {}).get("devices", []):
            if device["device"] == self.sensor_name:
                device["status"] = "on"
                break
        self.async_write_ha_state()

    async def async_turn_off(self, **_: Any) -> None:
        await self.coordinator.async_send_data(
            METHODS.MACHINE_DEVICE_POWER_POST_DEVICE,
            {"device": self.sensor_name, "action": "off"},
        )
        for device in self.coordinator.data.get("power_devices", {}).get("devices", []):
            if device["device"] == self.sensor_name:
                device["status"] = "off"
                break
        self.async_write_ha_state()


class MoonrakerDigitalOutputPin(MoonrakerSwitchSensor):
    """Moonraker power device switch class."""

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator, entry, description)
        self.pin = description.sensor_name.replace("output_pin ", "")

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.data.get("status", {})
            .get(self.sensor_name, {})
            .get("value") == 1
        )

    async def async_turn_on(self, **_: Any) -> None:
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"SET_PIN PIN={self.pin} VALUE=1"},
        )
        # optimistic update
        if "status" in self.coordinator.data and self.sensor_name in self.coordinator.data["status"]:
            self.coordinator.data["status"][self.sensor_name]["value"] = 1
        self.async_write_ha_state()

    async def async_turn_off(self, **_: Any) -> None:
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"SET_PIN PIN={self.pin} VALUE=0"},
        )
        if "status" in self.coordinator.data and self.sensor_name in self.coordinator.data["status"]:
            self.coordinator.data["status"][self.sensor_name]["value"] = 0
        self.async_write_ha_state()
