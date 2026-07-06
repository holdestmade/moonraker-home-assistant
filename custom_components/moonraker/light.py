"""Light platform for Moonraker integration."""

import logging
from dataclasses import dataclass
from typing import Optional

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    LightEntity,
    LightEntityDescription,
    ColorMode,
)
from homeassistant.core import callback

from .const import DOMAIN, METHODS
from .entity import BaseMoonrakerEntity
from .helpers import get_config_settings, get_object_list, is_output_pin

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MoonrakerLightSensorDescription(LightEntityDescription):
    """Class describing Moonraker light entities."""

    color_mode: Optional[ColorMode] = None
    sensor_name: Optional[str] = None
    icon: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None


def _is_output_pin_named_like_led(obj: str) -> bool:
    """True iff *obj* is `output_pin <name>` and 'led' is one of name's tokens."""
    if not is_output_pin(obj):
        return False
    tokens = obj.split(" ", 1)[1].lower().split("_")
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
    object_list = await get_object_list(coordinator)
    settings = await get_config_settings(coordinator)

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

        if color_mode == ColorMode.UNKNOWN:
            # e.g. an [led] with only two of the RGB pins configured; drive
            # all channels together as a plain brightness light.
            color_mode = ColorMode.BRIGHTNESS

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
    object_list = await get_object_list(coordinator)
    settings = await get_config_settings(coordinator)

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
    """Moonraker LED class for led / neopixel / dotstar / PCA chips.

    Klipper reports these objects as ``color_data``: a list of
    ``[red, green, blue, white]`` float tuples (0.0-1.0), one per LED in the
    chain. The whole chain is driven together via ``SET_LED``.
    """

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self.sensor_name = description.sensor_name
        self.led_name = description.sensor_name.split(" ", 1)[-1]
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_icon = description.icon
        self._attr_color_mode = description.color_mode
        self._attr_supported_color_modes = {description.color_mode}

    def _color_data(self) -> list | None:
        """Return [r, g, b, w] floats for the first LED in the chain."""
        try:
            return self.coordinator.data["status"][self.sensor_name]["color_data"][0]
        except (KeyError, IndexError, TypeError):
            return None

    @property
    def is_on(self) -> bool:
        color_data = self._color_data()
        return bool(color_data) and max(color_data) > 0

    @property
    def brightness(self) -> int | None:
        color_data = self._color_data()
        if not color_data:
            return None
        return int(max(color_data) * 255)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        color_data = self._color_data()
        if not color_data:
            return None
        scale = max(color_data[:3]) or 1.0
        return tuple(int(c / scale * 255) for c in color_data[:3])

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        color_data = self._color_data()
        if not color_data:
            return None
        scale = max(color_data) or 1.0
        return tuple(int(c / scale * 255) for c in color_data[:4])

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the LED with the requested color/brightness."""
        if ATTR_RGBW_COLOR in kwargs:
            rgbw = [c / 255 for c in kwargs[ATTR_RGBW_COLOR]]
        elif ATTR_RGB_COLOR in kwargs:
            rgbw = [c / 255 for c in kwargs[ATTR_RGB_COLOR]] + [0.0]
        elif self._attr_color_mode == ColorMode.BRIGHTNESS:
            rgbw = [1.0, 1.0, 1.0, 1.0]
        else:
            # Keep the current color; fall back to white when currently off.
            current = self._color_data()
            if current and max(current) > 0:
                rgbw = list(current[:4]) + [0.0] * (4 - len(current[:4]))
            elif self._attr_color_mode == ColorMode.RGBW:
                rgbw = [1.0, 1.0, 1.0, 1.0]
            else:
                rgbw = [1.0, 1.0, 1.0, 0.0]

        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            brightness = self.brightness or 255
        peak = max(rgbw) or 1.0
        rgbw = [min(1.0, c / peak * (brightness / 255)) for c in rgbw]

        await self._async_set_color(rgbw)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the LED."""
        await self._async_set_color([0.0, 0.0, 0.0, 0.0])

    async def _async_set_color(self, rgbw: list[float]) -> None:
        red, green, blue, white = (round(c, 4) for c in rgbw)
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {
                "script": (
                    f"SET_LED LED={self.led_name} RED={red} GREEN={green} "
                    f"BLUE={blue} WHITE={white} SYNC=0 TRANSMIT=1"
                )
            },
        )
        # optimistic local update for the whole chain
        status = self.coordinator.data.get("status", {}).get(self.sensor_name)
        if status and status.get("color_data"):
            status["color_data"] = [
                [red, green, blue, white] for _ in status["color_data"]
            ]
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
