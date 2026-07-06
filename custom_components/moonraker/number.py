"""Number platform for Moonraker integration."""

import logging
from dataclasses import dataclass
from typing import Optional

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberDeviceClass,
    NumberMode,
)
from homeassistant.core import callback
from homeassistant.const import UnitOfTemperature, PERCENTAGE

from .const import DOMAIN, METHODS
from .entity import BaseMoonrakerEntity
from .helpers import get_config_settings, get_object_list, is_output_pin

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MoonrakerNumberSensorDescription(NumberEntityDescription):
    """Class describing Moonraker number entities."""

    sensor_name: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None
    unit: Optional[str] = None
    update_code: Optional[str] = None
    max_value: Optional[int] = None
    device_class: Optional[NumberDeviceClass] = None
    status_key: Optional[str] = None


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the number platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    builders: list = []
    await _collect_output_pin(coordinator, builders)
    await _collect_temperature_target(coordinator, builders)
    await _collect_speed_factor(coordinator, builders)
    await _collect_fan_speed(coordinator, builders)

    if not builders:
        return
    await coordinator.async_refresh()
    async_add_devices(b(coordinator, entry) for b in builders)


async def _collect_temperature_target(coordinator, builders):
    """Collect optional temp target numbers."""
    sensors: list[MoonrakerNumberSensorDescription] = []
    object_list = await get_object_list(coordinator)

    for obj in object_list.get("objects", []):
        if obj.startswith("heater_bed"):
            sensors.append(
                MoonrakerNumberSensorDescription(
                    key=f"{obj}_target",
                    sensor_name=obj,
                    name="Bed Target".title(),
                    status_key="target",
                    subscriptions=[(obj, "target")],
                    icon="mdi:radiator",
                    unit=UnitOfTemperature.CELSIUS,
                    update_code="M140 S",
                    max_value=130,
                    device_class=NumberDeviceClass.TEMPERATURE,
                )
            )
        elif obj.startswith("extruder"):
            extruder_val = "0" if obj == "extruder" else obj[-1]
            sensors.append(
                MoonrakerNumberSensorDescription(
                    key=f"{obj}_target",
                    sensor_name=obj,
                    name=f"{obj} Target".title(),
                    status_key="target",
                    subscriptions=[(obj, "target")],
                    icon="mdi:printer-3d-nozzle-heat",
                    unit=UnitOfTemperature.CELSIUS,
                    update_code=f"M104 T{extruder_val} S",
                    max_value=350,
                    device_class=NumberDeviceClass.TEMPERATURE,
                )
            )

    if sensors:
        coordinator.load_sensor_data(sensors)
        for desc in sensors:
            builders.append(lambda coord, ent, d=desc: MoonrakerNumber(coord, ent, d))


async def _collect_output_pin(coordinator, builders):
    """Collect PWM output_pin sliders only (non-PWM become switches)."""
    object_list = await get_object_list(coordinator)
    settings = await get_config_settings(coordinator)

    numbers: list[MoonrakerNumberSensorDescription] = []
    for obj in object_list.get("objects", []):
        if not is_output_pin(obj):
            continue

        conf = (
            settings.get("status", {})
            .get("configfile", {})
            .get("settings", {})
            .get(obj.lower(), {})
        )
        if not conf.get("pwm", False):
            continue

        numbers.append(
            MoonrakerNumberSensorDescription(
                key=obj,
                sensor_name=obj,
                name=obj.replace("_", " ").title(),
                icon="mdi:switch",
                subscriptions=[(obj, "value")],
                unit=PERCENTAGE,
                max_value=100,
            )
        )

    if numbers:
        coordinator.load_sensor_data(numbers)
        for desc in numbers:
            builders.append(
                lambda coord, ent, d=desc: MoonrakerPWMOutputPin(coord, ent, d)
            )


async def _collect_speed_factor(coordinator, builders):
    """Collect speed factor number entity."""
    object_list = await get_object_list(coordinator)
    if "gcode_move" not in object_list.get("objects", []):
        return

    desc = MoonrakerNumberSensorDescription(
        key="speed_factor",
        sensor_name="gcode_move",
        name="Speed Factor",
        status_key="speed_factor",
        subscriptions=[("gcode_move", "speed_factor")],
        icon="mdi:speedometer",
        unit=PERCENTAGE,
        update_code="M220 S",
        max_value=200,
    )
    coordinator.load_sensor_data([desc])
    builders.append(
        lambda coord, ent: MoonrakerNumber(coord, ent, desc, value_multiplier=100.0)
    )


async def _collect_fan_speed(coordinator, builders):
    """Collect fan speed number entity."""
    object_list = await get_object_list(coordinator)
    if "fan" not in object_list.get("objects", []):
        return

    desc = MoonrakerNumberSensorDescription(
        key="fan_speed",
        sensor_name="fan",
        name="Fan Speed",
        status_key="speed",
        subscriptions=[("fan", "speed")],
        icon="mdi:fan",
        unit=PERCENTAGE,
        update_code="M106 S",
        max_value=100,
    )
    coordinator.load_sensor_data([desc])
    builders.append(
        lambda coord, ent: MoonrakerFanSpeed(coord, ent, desc, value_multiplier=100.0)
    )


class MoonrakerPWMOutputPin(BaseMoonrakerEntity, NumberEntity):
    """Moonraker PWM output pin class."""

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator, entry)
        self.pin = description.sensor_name.replace("output_pin ", "")
        self._attr_mode = NumberMode.SLIDER
        try:
            self._attr_native_value = (
                coordinator.data["status"][description.sensor_name]["value"] * 100
            )
        except (KeyError, TypeError):
            self._attr_native_value = None
        self.entity_description = description
        self.sensor_name = description.sensor_name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_icon = description.icon
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_native_max_value = 100

    async def async_set_native_value(self, value: float) -> None:
        """Set native Value (0-100)."""
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"SET_PIN PIN={self.pin} VALUE={round(value / 100, 2)}"},
        )
        # optimistic local update
        self._attr_native_value = value
        if "status" in self.coordinator.data and self.sensor_name in self.coordinator.data["status"]:
            self.coordinator.data["status"][self.sensor_name]["value"] = round(value / 100, 2)
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            self._attr_native_value = (
                self.coordinator.data["status"][self.sensor_name]["value"] * 100
            )
        except (KeyError, TypeError):
            pass  # keep last known value while data is incomplete
        self.async_write_ha_state()


class MoonrakerNumber(BaseMoonrakerEntity, NumberEntity):
    """Generic Moonraker number class."""

    def __init__(self, coordinator, entry, description, value_multiplier: float = 1.0) -> None:
        super().__init__(coordinator, entry)
        self._attr_mode = NumberMode.SLIDER
        try:
            self._attr_native_value = (
                coordinator.data["status"][description.sensor_name][description.status_key]
                * value_multiplier
            )
        except (KeyError, TypeError):
            self._attr_native_value = None
        self.entity_description = description
        self.sensor_name = description.sensor_name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_icon = description.icon
        self._attr_native_max_value = description.max_value
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.unit
        self.update_string = description.update_code
        self.value_multiplier = value_multiplier

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"{self.update_string}{value}"},
        )
        # optimistic
        self._attr_native_value = value
        if (
            "status" in self.coordinator.data
            and self.sensor_name in self.coordinator.data["status"]
            and self.entity_description.status_key in self.coordinator.data["status"][self.sensor_name]
        ):
            self.coordinator.data["status"][self.sensor_name][
                self.entity_description.status_key
            ] = value / self.value_multiplier
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            self._attr_native_value = (
                self.coordinator.data["status"][self.sensor_name][
                    self.entity_description.status_key
                ]
                * self.value_multiplier
            )
        except (KeyError, TypeError):
            pass  # keep last known value while data is incomplete
        self.async_write_ha_state()


class MoonrakerFanSpeed(MoonrakerNumber):
    """Moonraker fan speed number class."""

    async def async_set_native_value(self, value: float) -> None:
        """Set fan speed using 0–255 scale via M106."""
        adjusted_value = int(255 * (value / 100))
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"{self.update_string}{adjusted_value}"},
        )
        # optimistic
        self._attr_native_value = value
        if "status" in self.coordinator.data and "fan" in self.coordinator.data["status"]:
            self.coordinator.data["status"]["fan"]["speed"] = value / 100
        self.async_write_ha_state()
