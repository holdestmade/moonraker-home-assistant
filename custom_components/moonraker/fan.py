"""Fan platform for Moonraker integration."""

import logging
from typing import Any, Optional
from dataclasses import dataclass

from homeassistant.components.fan import (
    FanEntity,
    FanEntityDescription,
    FanEntityFeature,
)
from homeassistant.core import callback

from .const import DOMAIN, METHODS, OBJ
from .entity import BaseMoonrakerEntity

_LOGGER = logging.getLogger(__name__)


# -------- small helpers (module-local) --------
async def _get_object_list(coordinator) -> dict:
    cache_key = "_cached_object_list"
    if cache_key not in coordinator.data:
        coordinator.data[cache_key] = await coordinator.async_fetch_data(
            METHODS.PRINTER_OBJECTS_LIST
        )
    return coordinator.data[cache_key]


async def _get_config_settings(coordinator) -> dict:
    cache_key = "_cached_config_settings"
    if cache_key not in coordinator.data:
        query_obj = {OBJ: {"configfile": ["settings"]}}
        coordinator.data[cache_key] = await coordinator.async_fetch_data(
            METHODS.PRINTER_OBJECTS_QUERY, query_obj, quiet=True
        )
    return coordinator.data[cache_key]
# ---------------------------------------------


@dataclass
class MoonrakerFanDescription(FanEntityDescription):
    """Class describing Moonraker fan entities."""

    sensor_name: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the fan platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await async_setup_output_pin_fan(coordinator, entry, async_add_entities)


async def async_setup_output_pin_fan(coordinator, entry, async_add_entities):
    """Set up fans for PWM-enabled output_pins with 'fan' in name."""
    object_list = await _get_object_list(coordinator)
    settings = await _get_config_settings(coordinator)

    fans: list[MoonrakerFanDescription] = []
    for obj in object_list.get("objects", []):
        if "output_pin" not in obj or "fan" not in obj.lower():
            continue
        if not settings["status"]["configfile"]["settings"][obj.lower()].get("pwm", False):
            continue

        fans.append(
            MoonrakerFanDescription(
                key=f"fan_{obj}",
                sensor_name=obj,
                name=obj.replace("_", " ").title(),
                subscriptions=[(obj, "value")],
                entity_registry_enabled_default=True,
            )
        )

    if fans:
        coordinator.load_sensor_data(fans)
        await coordinator.async_refresh()
        async_add_entities([MoonrakerOutputPinFan(coordinator, entry, desc) for desc in fans])


class MoonrakerOutputPinFan(BaseMoonrakerEntity, FanEntity):
    """Moonraker output_pin fan class."""

    def __init__(self, coordinator, entry, description):
        super().__init__(coordinator, entry)
        self.pin_name = description.sensor_name.replace("output_pin ", "")
        self.entity_description = description
        self.sensor_name = description.sensor_name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_supported_features = (
            FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        )
        self._attr_percentage = self.percentage  # seed from cache

    @property
    def is_on(self) -> bool:
        return (self.percentage or 0) > 0

    @property
    def percentage(self) -> int | None:
        value = self.coordinator.data.get("status", {}).get(self.sensor_name, {}).get("value")
        return int(value * 100) if value is not None else 0

    async def async_set_percentage(self, percentage: int) -> None:
        """Set speed (optimistic)."""
        value = round(percentage / 100.0, 2)
        await self.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT,
            {"script": f"SET_PIN PIN={self.pin_name} VALUE={value}"},
        )
        # optimistic cache + state
        if "status" in self.coordinator.data and self.sensor_name in self.coordinator.data["status"]:
            self.coordinator.data["status"][self.sensor_name]["value"] = value
        self._attr_percentage = percentage
        self.async_write_ha_state()

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        if percentage is None:
            percentage = 100
        await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set_percentage(0)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_percentage = self.percentage
        self.async_write_ha_state()
