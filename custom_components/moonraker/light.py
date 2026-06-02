"""Light platform for Moonraker integration."""

import logging
from dataclasses import dataclass
from typing import Optional

from homeassistant.components.light import (
    LightEntity,
    LightEntityDescription,
    ColorMode,
)
from homeassistant.core import callback

from .const import DOMAIN, METHODS, OBJ
from .entity import BaseMoonrakerEntity

_LOGGER = logging.getLogger(__name__)


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


@dataclass(frozen=True, kw_only=True)
class MoonrakerLightSensorDescription(LightEntityDescription):
    """Class describing Moonraker light entities."""

    color_mode: Optional[ColorMode] = None
    sensor_name: Optional[str] = None
    icon: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None


def _is_output_pin_named_like_led(obj: str) -> bool:
    """True iff *obj* is `output_pin <name>` and 'led' is one of name's tokens."""
    parts = obj.split(" ", 1)
    if len(parts) != 2 or parts[0] != "output_pin":
        return False
    tokens = parts[1].lower().split("_")
    return "led" in tokens


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up the light platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    builders: list = []
    await _collect_led_like(coordinator, builders)
    await _collect_output_pin_light(coordinator, builders)
    if not builders:
        return
    await coordinator.async_refresh()
    async_add_devices(b(coordinator, entry) for b in builders)


async def _collect_led_like(coordinator, builders):
    """Collect entities for led / neopixel / dotstar / PCA leds."""
    object_list = await _get_object_list(coordinator)
    settings = await _get_config_settings(coordinator)

    lights: list[MoonrakerLightSensorDescription] = []
    for obj in object_list.get("objects", []):
        if not (
            obj.startswith("led ")
            or obj.startswith("neopixel ")
            or obj.startswith("dotstar ")
            or obj.startswith("pca9533 ")
            or obj.startswith("pca9632 ")
        ):
            continue

        led_type = obj.split()[0]
        color_mode = ColorMode.UNKNOWN
        conf = (
            settings.get("status", {})
            .get("configfile", {})
            .get("settings", {})
            .get(obj.lower(), {})
        )
        if not conf:
            continue

        if led_type == "led":
            num_led_pins = sum(
                1 for pin in ["red_pin", "green_pin", "blue_pin", "white_pin"] if pin in conf
            )
            if num_led_pins == 0:
                continue
            elif num_led_pins == 1:
                color_mode = ColorMode.BRIGHTNESS
            elif num_led_pins == 4 or "white_pin" in conf:
                color_mode = ColorMode.RGBW
            elif all(p in conf for p in ("red_pin", "green_pin", "blue_pin")):
                color_mode = ColorMode.RGB
        elif led_type in ("neopixel", "pca9632"):
            if "color_order" in conf and "W" in conf["color_order"]:
                color_mode = ColorMode.RGBW
            else:
                color_mode = ColorMode.RGB
        elif led_type == "dotstar":
            color_mode = ColorMode.RGB
        elif led_type == "pca9533":
            color_mode = ColorMode.RGBW

        lights.append(
            MoonrakerLightSensorDescription(
                key=obj,
                sensor_name=obj,
                name=obj.replace("_", " ").title(),
                icon="mdi:led-variant-on",
                subscriptions=[(obj, "color_data")],
                color_mode=color_mode,
            )
        )

    if lights:
        coordinator.load_sensor_data(lights)
        for desc in lights:
            builders.append(lambda coord, ent, d=desc: MoonrakerLED(coord, ent, d))


async def _collect_output_pin_light(coordinator, builders):
    """Collect lights for PWM-enabled output_pins (e.g., LED strips)."""
    object_list = await _get_object_list(coordinator)
    settings = await _get_config_settings(coordinator)

    lights: list[MoonrakerLightSensorDescription] = []
    for obj in object_list.get("objects", []):
        if not _is_output_pin_named_like_led(obj):
            continue
        conf = (
            settings.get("status", {})
            .get("configfile", {})
            .get("settings", {})
            .get(obj.lower(), {})
        )
        if not conf.get("pwm", False):
            continue

        lights.append(
            MoonrakerLightSensorDescription(
                key=f"light_{obj}",
                sensor_name=obj,
                name=obj.replace("_", " ").title(),
                icon="mdi:lightbulb",
                subscriptions=[(obj, "value")],
                color_mode=ColorMode.BRIGHTNESS,
            )
        )

    if lights:
        coordinator.load_sensor_data(lights)
        for desc in lights:
            builders.append(
                lambda coord, ent, d=desc: MoonrakerOutputPinLight(coord, ent, d)
            )


class MoonrakerOutputPinLight(BaseMoonrakerEntity, LightEntity):
    """Moonraker output_pin light class."""

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator, entry)
        self.pin_name = description.sensor_name.replace("output_pin ", "")
        self.entity_description = description
        self.sensor_name = description.sensor_name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_icon = description.icon
        self._attr_color_mode = description.color_mode
        self._attr_supported_color_modes = {description.color_mode}
        self._sync_from_coordinator()

    async def async_turn_on(self, brightness: int | None = None, **kwargs) -> None:
        """Turn on (optimistic)."""
        if brightness is None:
            brightness = 255
        value = round(brightness / 255.0, 2)
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"SET_PIN PIN={self.pin_name} VALUE={value}"},
        )
        if "status" in self.coordinator.data and self.sensor_name in self.coordinator.data["status"]:
            self.coordinator.data["status"][self.sensor_name]["value"] = value
        self._attr_is_on = True
        self._attr_brightness = brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off (optimistic)."""
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"SET_PIN PIN={self.pin_name} VALUE=0"},
        )
        if "status" in self.coordinator.data and self.sensor_name in self.coordinator.data["status"]:
            self.coordinator.data["status"][self.sensor_name]["value"] = 0
        self._attr_is_on = False
        self._attr_brightness = 0
        self.async_write_ha_state()

    def _sync_from_coordinator(self) -> None:
        """Set attributes from coordinator cache."""
        value = self.coordinator.data.get("status", {}).get(self.sensor_name, {}).get("value")
        if value is None:
            self._attr_is_on = False
            self._attr_brightness = 0
            return
        self._attr_is_on = value > 0
        self._attr_brightness = int(value * 255)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_from_coordinator()
        self.async_write_ha_state()


class MoonrakerLED(BaseMoonrakerEntity, LightEntity):
    """Moonraker LED class (placeholder for full RGB/RGBW support)."""
    pass
