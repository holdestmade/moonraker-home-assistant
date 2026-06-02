"""Button platform for Moonraker integration."""

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription

from .const import DOMAIN, METHODS
from .entity import BaseMoonrakerEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MoonrakerButtonDescription(ButtonEntityDescription):
    """Class describing Moonraker button entities."""
    press_fn: Callable | None = None
    button_name: str | None = None
    icon: str | None = None


# Core buttons that don’t depend on optional API features
BUTTONS: tuple[MoonrakerButtonDescription, ...] = (
    MoonrakerButtonDescription(
        key="emergency_stop",
        name="Emergency Stop",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_EMERGENCY_STOP
        ),
        icon="mdi:alert-octagon-outline",
    ),
    MoonrakerButtonDescription(
        key="pause_print",
        name="Pause Print",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_PRINT_PAUSE
        ),
        icon="mdi:pause",
    ),
    MoonrakerButtonDescription(
        key="resume_print",
        name="Resume Print",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_PRINT_RESUME
        ),
        icon="mdi:play",
    ),
    MoonrakerButtonDescription(
        key="cancel_print",
        name="Cancel Print",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_PRINT_CANCEL
        ),
        icon="mdi:stop",
    ),
    MoonrakerButtonDescription(
        key="server_restart",
        name="Server Restart",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.SERVER_RESTART
        ),
        icon="mdi:restart",
    ),
    MoonrakerButtonDescription(
        key="host_restart",
        name="Host Restart",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.HOST_RESTART
        ),
        icon="mdi:restart",
    ),
    MoonrakerButtonDescription(
        key="firmware_restart",
        name="Firmware Restart",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_FIRMWARE_RESTART
        ),
        icon="mdi:restart",
    ),
    MoonrakerButtonDescription(
        key="host_shutdown",
        name="Host Shutdown",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.HOST_SHUTDOWN
        ),
        icon="mdi:power",
    ),
    MoonrakerButtonDescription(
        key="machine_update_refresh",
        name="Machine Update Refresh",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.MACHINE_UPDATE_REFRESH
        ),
        icon="mdi:refresh",
    ),
    MoonrakerButtonDescription(
        key="reset_totals",
        name="Reset Totals",
        entity_registry_enabled_default=False,
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.SERVER_HISTORY_RESET_TOTALS
        ),
        icon="mdi:history",
    ),
    MoonrakerButtonDescription(
        key="start_print_from_queue",
        name="Start Print from Queue",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.SERVER_JOB_QUEUE_START
        ),
        icon="mdi:playlist-play",
    ),
    # Homing helpers
    MoonrakerButtonDescription(
        key="home_x_axis",
        name="Home X Axis",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT, {"script": "G28 X"}
        ),
        icon="mdi:axis-x-arrow",
        entity_registry_enabled_default=True,
    ),
    MoonrakerButtonDescription(
        key="home_y_axis",
        name="Home Y Axis",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT, {"script": "G28 Y"}
        ),
        icon="mdi:axis-y-arrow",
        entity_registry_enabled_default=True,
    ),
    MoonrakerButtonDescription(
        key="home_z_axis",
        name="Home Z Axis",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT, {"script": "G28 Z"}
        ),
        icon="mdi:axis-z-arrow",
        entity_registry_enabled_default=True,
    ),
    MoonrakerButtonDescription(
        key="home_all_axes",
        name="Home All Axes",
        press_fn=lambda button: button.coordinator.async_send_data(
            METHODS.PRINTER_GCODE_SCRIPT, {"script": "G28"}
        ),
        icon="mdi:axis-arrow",
        entity_registry_enabled_default=True,
    ),
)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Moonraker buttons."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # Static buttons
    await async_setup_basic_buttons(coordinator, entry, async_add_entities)

    # Buttons that depend on optional API calls
    await async_setup_macros(coordinator, entry, async_add_entities)
    await async_setup_services(coordinator, entry, async_add_entities)


async def async_setup_basic_buttons(coordinator, entry, async_add_entities):
    async_add_entities([MoonrakerButton(coordinator, entry, desc) for desc in BUTTONS])


async def async_setup_macros(coordinator, entry, async_add_entities):
    """Create a button per Klipper gcode_macro."""
    macros: list[MoonrakerButtonDescription] = []
    try:
        cmds = await coordinator.async_fetch_data(METHODS.PRINTER_GCODE_HELP)
    except Exception as exc:
        _LOGGER.debug("Skipping macro buttons; failed to fetch help: %s", exc)
        cmds = {}

    for cmd, description in cmds.items():
        # Enable by default only for real user macros
        enable_by_default = description == "G-Code macro"
        macros.append(
            MoonrakerButtonDescription(
                key=cmd,
                name=f"Macro {cmd.lower().replace('_', ' ').title()}",
                press_fn=lambda button: button.coordinator.async_send_data(
                    METHODS.PRINTER_GCODE_SCRIPT, {"script": button.invoke_name}
                ),
                icon="mdi:play",
                entity_registry_enabled_default=enable_by_default,
            )
        )

    if macros:
        async_add_entities([MoonrakerButton(coordinator, entry, d) for d in macros])


async def async_setup_services(coordinator, entry, async_add_entities):
    """Create Start/Stop/Restart buttons for allowed services."""
    service_buttons: list[MoonrakerButtonDescription] = []
    try:
        system_info = await coordinator.async_fetch_data(METHODS.MACHINE_SYSTEM_INFO)
        available_services = system_info["system_info"].get("available_services", [])
    except Exception as exc:
        _LOGGER.debug("Skipping service control buttons; failed to fetch services: %s", exc)
        available_services = []

    for service in available_services:
        service_buttons.extend(
            [
                MoonrakerButtonDescription(
                    key=f"stop_{service.lower()}",
                    name=f"Stop {service}",
                    press_fn=lambda button, svc=service: button.coordinator.async_send_data(
                        METHODS.MACHINE_SERVICES_STOP, {"service": svc}
                    ),
                    icon="mdi:stop-circle-outline",
                    entity_registry_visible_default=False,
                ),
                MoonrakerButtonDescription(
                    key=f"start_{service.lower()}",
                    name=f"Start {service}",
                    press_fn=lambda button, svc=service: button.coordinator.async_send_data(
                        METHODS.MACHINE_SERVICES_START, {"service": svc}
                    ),
                    icon="mdi:play-circle-outline",
                    entity_registry_visible_default=False,
                ),
                MoonrakerButtonDescription(
                    key=f"restart_{service.lower()}",
                    name=f"Restart {service}",
                    press_fn=lambda button, svc=service: button.coordinator.async_send_data(
                        METHODS.MACHINE_SERVICES_RESTART, {"service": svc}
                    ),
                    icon="mdi:restart",
                    entity_registry_visible_default=False,
                ),
            ]
        )

    if service_buttons:
        async_add_entities([MoonrakerButton(coordinator, entry, d) for d in service_buttons])


class MoonrakerButton(BaseMoonrakerEntity, ButtonEntity):
    """Moonraker button entity."""

    def __init__(self, coordinator, entry, description: MoonrakerButtonDescription):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._attr_icon = description.icon
        self.entity_description = description

        # Saved so press lambdas don’t close over loop vars
        self.invoke_name = description.key
        self.press_fn = description.press_fn

    @property
    def available(self) -> bool:
        """
        Buttons should be pressable even if the last poll failed.
        We report available whenever the integration is loaded.
        """
        return True

    async def async_press(self) -> None:
        """Send the action to Moonraker."""
        await self.press_fn(self)
