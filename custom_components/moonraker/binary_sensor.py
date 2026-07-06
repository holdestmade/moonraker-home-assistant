"""Binary sensors platform for Moonraker integration."""
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN, METHODS
from .entity import BaseMoonrakerEntity
from .helpers import get_object_list

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MoonrakerBinarySensorDescription(BinarySensorEntityDescription):
    """Class describing Moonraker binary_sensor entities."""

    is_on_fn: Optional[Callable] = None
    sensor_name: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None
    icon: Optional[str] = None


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the binary_sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    descs: list[MoonrakerBinarySensorDescription] = []
    await _collect_optional_binary_sensors(coordinator, descs)
    await _collect_update_binary_sensor(coordinator, descs)
    if not descs:
        return
    await coordinator.async_refresh()
    async_add_devices(MoonrakerBinarySensor(coordinator, entry, d) for d in descs)


async def _collect_optional_binary_sensors(coordinator, descs):
    """Collect optional filament sensors."""
    object_list = await get_object_list(coordinator)
    new_descs: list[MoonrakerBinarySensorDescription] = []
    for obj in object_list.get("objects", []):
        split_obj = obj.split()
        if split_obj[0] in ["filament_switch_sensor", "filament_motion_sensor"]:
            new_descs.append(
                MoonrakerBinarySensorDescription(
                    key=f"{split_obj[0]}_{split_obj[1]}",
                    sensor_name=obj,
                    is_on_fn=lambda sensor: sensor.coordinator.data["status"][sensor.sensor_name]["filament_detected"],
                    name=split_obj[1].replace("_", " ").title(),
                    subscriptions=[(obj, "filament_detected")],
                    icon="mdi:printer-3d-nozzle-alert",
                    device_class=BinarySensorDeviceClass.OCCUPANCY,
                )
            )
    if new_descs:
        coordinator.load_sensor_data(new_descs)
        descs.extend(new_descs)


async def _machine_update_updater(coordinator, _data):
    return {
        "machine_update": await coordinator.async_fetch_data(
            METHODS.MACHINE_UPDATE_STATUS, quiet=True
        )
    }


async def _collect_update_binary_sensor(coordinator, descs):
    """Collect the Update Available binary sensor."""
    # Probe first: if the update manager is disabled, polling it every cycle
    # would fail the whole coordinator refresh.
    try:
        status = await coordinator.async_fetch_data(
            METHODS.MACHINE_UPDATE_STATUS, quiet=True
        )
    except UpdateFailed as exc:
        _LOGGER.debug("Skipping update binary sensor: %s", exc)
        return
    if not isinstance(status, dict) or status.get("error"):
        return

    coordinator.add_data_updater(_machine_update_updater)
    desc = MoonrakerBinarySensorDescription(
        key="update_available",
        sensor_name="update_available",
        is_on_fn=update_available_fn,
        name="Update Available",
        subscriptions=[],
        icon="mdi:update",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_registry_enabled_default=False,
    )
    coordinator.load_sensor_data([desc])
    descs.append(desc)


def update_available_fn(sensor):
    """Return if update is available."""
    version_info = sensor.coordinator.data.get("machine_update", {}).get("version_info")
    if not version_info:
        return False

    for component, info in version_info.items():
        if component == "system":
            if info.get("package_count", 0) > 0:
                return True
            continue

        remote_version = info.get("remote_version")
        if remote_version is not None and remote_version != info.get("version"):
            return True

    return False


class MoonrakerBinarySensor(BaseMoonrakerEntity, BinarySensorEntity):
    """Moonraker binary_sensor class."""

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self.sensor_name = description.sensor_name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_icon = description.icon
        self._attr_device_class = description.device_class

    @property
    def is_on(self) -> bool | None:
        try:
            return bool(self.entity_description.is_on_fn(self))
        except (KeyError, TypeError):
            return None
