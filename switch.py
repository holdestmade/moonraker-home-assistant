"""Switch platform for Moonraker integration."""
from dataclasses import dataclass
from typing import Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription

from .const import DOMAIN, METHODS, OBJ
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


async def _get_config_settings(coordinator) -> dict:
    cache_key = "_cached_config_settings"
    if cache_key not in coordinator.data:
        query_obj = {OBJ: {"configfile": ["settings"]}}
        resp = await coordinator.async_fetch_data(
            METHODS.PRINTER_OBJECTS_QUERY, query_obj, quiet=True
        )
        coordinator.data[cache_key] = resp if isinstance(resp, dict) else {}
    return coordinator.data[cache_key]
# ---------------------------------------------


@dataclass
class MoonrakerSwitchSensorDescription(SwitchEntityDescription):
    """Class describing Moonraker switch entities."""

    sensor_name: Optional[str] = None
    icon: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the switch platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    await async_setup_power_device(coordinator, entry, async_add_devices)
    await async_setup_output_pin(coordinator, entry, async_add_devices)


async def _power_device_updater(coordinator):
    return {
        "power_devices": await coordinator.async_fetch_data(
            METHODS.MACHINE_DEVICE_POWER_DEVICES
        )
    }


async def async_setup_output_pin(coordinator, entry, async_add_entities):
    """Set optional binary sensor platform."""
    object_list = await _get_object_list(coordinator)
    settings = await _get_config_settings(coordinator)

    switches = []
    for obj in object_list.get("objects", []):
        if "output_pin" not in obj:
            continue

        conf = (
            settings.get("status", {})
            .get("configfile", {})
            .get("settings", {})
            .get(obj.lower(), {})
        )
        if conf.get("pwm", False):
            continue

        desc = MoonrakerSwitchSensorDescription(
            key=obj,
            sensor_name=obj,
            name=obj.replace("_", " ").title(),
            icon="mdi:switch",
            subscriptions=[(obj, "value")],
        )
        switches.append(desc)

    coordinator.load_sensor_data(switches)
    await coordinator.async_refresh()
    async_add_entities(
        [MoonrakerDigitalOutputPin(coordinator, entry, desc) for desc in switches]
    )


async def async_setup_power_device(coordinator, entry, async_add_entities):
    """Set optional binary sensor platform."""
    power_devices = await coordinator.async_fetch_data(
        METHODS.MACHINE_DEVICE_POWER_DEVICES
    )
    if power_devices.get("error"):
        return

    coordinator.add_data_updater(_power_device_updater)

    sensors = []
    for device in power_devices["devices"]:
        desc = MoonrakerSwitchSensorDescription(
            key=device["device"],
            sensor_name=device["device"],
            name=device["device"].replace("_", " ").title(),
            icon="mdi:power",
            subscriptions=[],
        )
        sensors.append(desc)

    coordinator.load_sensor_data(sensors)
    await coordinator.async_refresh()
    async_add_entities(
        [MoonrakerPowerDeviceSwitchSensor(coordinator, entry, desc) for desc in sensors]
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

    async def async_turn_on(self, **_: any) -> None:
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

    async def async_turn_off(self, **_: any) -> None:
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
        return self.coordinator.data["status"][self.sensor_name]["value"] == 1

    async def async_turn_on(self, **_: any) -> None:
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"SET_PIN PIN={self.pin} VALUE=1"},
        )
        # optimistic update
        if "status" in self.coordinator.data and self.sensor_name in self.coordinator.data["status"]:
            self.coordinator.data["status"][self.sensor_name]["value"] = 1
        self.async_write_ha_state()

    async def async_turn_off(self, **_: any) -> None:
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"SET_PIN PIN={self.pin} VALUE=0"},
        )
        if "status" in self.coordinator.data and self.sensor_name in self.coordinator.data["status"]:
            self.coordinator.data["status"][self.sensor_name]["value"] = 0
        self.async_write_ha_state()
