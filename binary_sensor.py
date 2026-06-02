"""Binary sensors platform for Moonraker integration."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)

from .const import DOMAIN, METHODS
from .entity import BaseMoonrakerEntity


# -------- small helpers (module-local) --------
async def _get_object_list(coordinator) -> dict:
    cache_key = "_cached_object_list"
    if cache_key not in coordinator.data:
        resp = await coordinator.async_fetch_data(METHODS.PRINTER_OBJECTS_LIST)
        if not isinstance(resp, dict) or "objects" not in resp:
            resp = {"objects": []}
        coordinator.data[cache_key] = resp
    return coordinator.data[cache_key]
# ---------------------------------------------


@dataclass
class MoonrakerBinarySensorDescription(BinarySensorEntityDescription):
    """Class describing Moonraker binary_sensor entities."""

    is_on_fn: Optional[Callable] = None
    sensor_name: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None
    icon: Optional[str] = None


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the binary_sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await async_setup_optional_binary_sensors(coordinator, entry, async_add_devices)
    await async_setup_update_binary_sensors(coordinator, entry, async_add_devices)


async def async_setup_optional_binary_sensors(coordinator, entry, async_add_entities):
    """Set optional filament sensors."""
    sensors = []
    object_list = await _get_object_list(coordinator)
    for obj in object_list.get("objects", []):
        split_obj = obj.split()
        if split_obj[0] in ["filament_switch_sensor", "filament_motion_sensor"]:
            sensors.append(
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

    coordinator.load_sensor_data(sensors)
    await coordinator.async_refresh()
    async_add_entities([MoonrakerBinarySensor(coordinator, entry, desc) for desc in sensors])


async def async_setup_update_binary_sensors(coordinator, entry, async_add_entities):
    """Update available."""
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
    await coordinator.async_refresh()
    async_add_entities([MoonrakerBinarySensor(coordinator, entry, desc)])


def update_available_fn(sensor):
    """Return if update is available."""
    if "machine_update" not in sensor.coordinator.data:
        return False

    for component in sensor.coordinator.data["machine_update"]["version_info"]:
        if component == "system":
            if sensor.coordinator.data["machine_update"]["version_info"][component]["package_count"] > 0:
                return True
            continue

        if (
            sensor.coordinator.data["machine_update"]["version_info"][component]["remote_version"]
            != sensor.coordinator.data["machine_update"]["version_info"][component]["version"]
        ):
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
    def is_on(self) -> bool:
        return bool(self.entity_description.is_on_fn(self))
