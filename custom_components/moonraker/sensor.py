"""Sensor platform for Moonraker integration."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    REVOLUTIONS_PER_MINUTE,
)
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import OBJ, DOMAIN, METHODS, PRINTERSTATES, PRINTSTATES
from .entity import BaseMoonrakerEntity

_LOGGER = logging.getLogger(__name__)


# -------- small helpers (module-local) --------
async def _get_object_list(coordinator) -> dict:
    """Fetch and cache PRINTER_OBJECTS_LIST safely (guarantee {'objects': [...]})"""
    cache_key = "_cached_object_list"
    if cache_key not in coordinator.data:
        try:
            resp = await coordinator.async_fetch_data(METHODS.PRINTER_OBJECTS_LIST)
        except UpdateFailed:
            resp = {"objects": []}
        if not isinstance(resp, dict) or "objects" not in resp:
            resp = {"objects": []}
        coordinator.data[cache_key] = resp
    return coordinator.data[cache_key]


async def _get_config_for_obj(coordinator, obj: str, fields: list[str] | None = None) -> dict:
    """Query a specific object once per setup call; cache by object+fields."""
    key_fields = ",".join(fields or [])
    cache_key = f"_cached_query_{obj}_{key_fields}"
    if cache_key not in coordinator.data:
        query_obj = {OBJ: {obj: fields}}
        try:
            resp = await coordinator.async_fetch_data(
                METHODS.PRINTER_OBJECTS_QUERY, query_obj, quiet=True
            )
        except UpdateFailed:
            resp = {}
        coordinator.data[cache_key] = resp if isinstance(resp, dict) else {}
    return coordinator.data[cache_key]
# ---------------------------------------------


@dataclass(frozen=True, kw_only=True)
class MoonrakerSensorDescription(SensorEntityDescription):
    """Class describing Moonraker sensor entities."""

    value_fn: Optional[Callable] = None
    sensor_name: Optional[str] = None
    status_key: Optional[str] = None
    icon: Optional[str] = None
    unit: Optional[str] = None
    device_class: Optional[str] = None
    subscriptions: Optional[list[tuple[str, ...]]] = None


SENSORS: tuple[MoonrakerSensorDescription, ...] = [
    MoonrakerSensorDescription(
        key="state",
        name="Printer State",
        value_fn=lambda sensor: sensor.coordinator.data["printer.info"]["state"],
        device_class=SensorDeviceClass.ENUM,
        options=PRINTERSTATES.list(),
        subscriptions=[],
    ),
    MoonrakerSensorDescription(
        key="message",
        name="Printer Message",
        value_fn=lambda sensor: sensor.coordinator.data["printer.info"]["state_message"],
        subscriptions=[],
    ),
    MoonrakerSensorDescription(
        key="print_state",
        name="Current Print State",
        value_fn=lambda sensor: sensor.coordinator.data["status"]["print_stats"]["state"],
        device_class=SensorDeviceClass.ENUM,
        options=PRINTSTATES.list(),
        subscriptions=[("print_stats", "state")],
    ),
    MoonrakerSensorDescription(
        key="print_message",
        name="Current Print Message",
        value_fn=lambda sensor: sensor.coordinator.data["status"]["print_stats"]["message"],
        subscriptions=[("print_stats", "message")],
    ),
    MoonrakerSensorDescription(
        key="display_message",
        name="Current Display Message",
        value_fn=lambda sensor: (
            sensor.coordinator.data["status"]["display_status"]["message"]
            if sensor.coordinator.data["status"]["display_status"]["message"] is not None
            else ""
        ),
        subscriptions=[("display_status", "message")],
    ),
    MoonrakerSensorDescription(
        key="filename",
        name="Filename",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            sensor.coordinator.data["status"]["print_stats"]["filename"]
        ),
        subscriptions=[("print_stats", "filename")],
    ),
    MoonrakerSensorDescription(
        key="print_projected_total_duration",
        name="Print Projected Total Duration",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            round(
                sensor.coordinator.data["status"]["print_stats"]["print_duration"]
                / calculate_pct_job(sensor.coordinator.data)
                if calculate_pct_job(sensor.coordinator.data) > 0
                else 0,
                2,
            )
            / 3600
        ),
        subscriptions=[
            ("print_stats", "total_duration"),
            ("display_status", "progress"),
            ("virtual_sdcard", "progress"),
        ],
        icon="mdi:timer",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
    ),
    MoonrakerSensorDescription(
        key="print_time_left",
        name="Print Time Left",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            round(
                (
                    sensor.coordinator.data["status"]["print_stats"]["print_duration"]
                    / calculate_pct_job(sensor.coordinator.data)
                    if calculate_pct_job(sensor.coordinator.data) > 0
                    else 0
                )
                - sensor.coordinator.data["status"]["print_stats"]["print_duration"],
                2,
            )
            / 3600
        ),
        subscriptions=[
            ("print_stats", "print_duration"),
            ("display_status", "progress"),
            ("virtual_sdcard", "progress"),
        ],
        icon="mdi:timer",
        unit=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
    ),
    MoonrakerSensorDescription(
        key="print_eta",
        name="Print ETA",
        value_fn=lambda sensor: calculate_eta(sensor.coordinator.data),
        subscriptions=[
            ("print_stats", "print_duration"),
            ("display_status", "progress"),
            ("virtual_sdcard", "progress"),
        ],
        icon="mdi:timer",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    MoonrakerSensorDescription(
        key="slicer_print_duration_estimate",
        name="Slicer Print Duration Estimate",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            round(sensor.coordinator.data["estimated_time"] / 3600, 2)
            if sensor.coordinator.data["estimated_time"] > 0
            else 0
        ),
        subscriptions=[],
        icon="mdi:timer",
        device_class=SensorDeviceClass.DURATION,
        unit=UnitOfTime.HOURS,
    ),
    MoonrakerSensorDescription(
        key="slicer_print_time_left_estimate",
        name="Slicer Print Time Left Estimate",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            round(
                (
                    sensor.coordinator.data["estimated_time"]
                    - sensor.coordinator.data["status"]["print_stats"]["print_duration"]
                )
                / 3600,
                2,
            )
            if sensor.coordinator.data["estimated_time"] > 0
            else 0
        ),
        subscriptions=[("print_stats", "print_duration")],
        icon="mdi:timer",
        device_class=SensorDeviceClass.DURATION,
        unit=UnitOfTime.HOURS,
    ),
    MoonrakerSensorDescription(
        key="print_duration",
        name="Print Duration",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            round(sensor.coordinator.data["status"]["print_stats"]["print_duration"] / 60, 2)
        ),
        subscriptions=[("print_stats", "print_duration")],
        icon="mdi:timer",
        unit=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
    ),
    MoonrakerSensorDescription(
        key="filament_used",
        name="Filament Used",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            round(
                int(sensor.coordinator.data["status"]["print_stats"]["filament_used"]) / 1000,
                2,
            )
        ),
        subscriptions=[("print_stats", "filament_used")],
        icon="mdi:tape-measure",
        unit=UnitOfLength.METERS,
    ),
    MoonrakerSensorDescription(
        key="progress",
        name="Progress",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            int(round(calculate_pct_job(sensor.coordinator.data) * 100))
        ),
        subscriptions=[("display_status", "progress"), ("virtual_sdcard", "progress")],
        icon="mdi:percent",
        unit=PERCENTAGE,
    ),
    MoonrakerSensorDescription(
        key="total_layer",
        name="Total Layer",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(
            sensor.coordinator.data["status"]["print_stats"]["info"]["total_layer"]
            if sensor.coordinator.data["status"]["print_stats"].get("info") is not None
            and "total_layer" in sensor.coordinator.data["status"]["print_stats"]["info"]
            and sensor.coordinator.data["status"]["print_stats"]["info"]["total_layer"] is not None
            and sensor.coordinator.data["status"]["print_stats"]["info"]["total_layer"] > 0
            else sensor.coordinator.data["layer_count"]
        ),
        subscriptions=[("print_stats", "info", "total_layer")],
        icon="mdi:layers-triple",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MoonrakerSensorDescription(
        key="current_layer",
        name="Current Layer",
        value_fn=lambda sensor: calculate_current_layer(sensor.coordinator.data),
        subscriptions=[("print_stats", "info", "current_layer")],
        icon="mdi:layers-edit",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MoonrakerSensorDescription(
        key="toolhead_position_x",
        name="Toolhead position X",
        value_fn=lambda sensor: round(sensor.coordinator.data["status"]["toolhead"]["position"][0], 2),
        subscriptions=[("toolhead", "position")],
        icon="mdi:axis-x-arrow",
        unit=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MoonrakerSensorDescription(
        key="toolhead_position_y",
        name="Toolhead position Y",
        value_fn=lambda sensor: round(sensor.coordinator.data["status"]["toolhead"]["position"][1], 2),
        subscriptions=[("toolhead", "position")],
        icon="mdi:axis-y-arrow",
        unit=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MoonrakerSensorDescription(
        key="toolhead_position_z",
        name="Toolhead position Z",
        value_fn=lambda sensor: round(sensor.coordinator.data["status"]["toolhead"]["position"][2], 2),
        subscriptions=[("toolhead", "position")],
        icon="mdi:axis-z-arrow",
        unit=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MoonrakerSensorDescription(
        key="object_height",
        name="Object Height",
        value_fn=lambda sensor: sensor.empty_result_when_not_printing(sensor.coordinator.data["object_height"]),
        subscriptions=[],
        icon="mdi:axis-z-arrow",
        device_class=SensorDeviceClass.DISTANCE,
        unit=UnitOfLength.MILLIMETERS,
    ),
    MoonrakerSensorDescription(
        key="sysload",
        name="System Load",
        value_fn=lambda sensor: round(sensor.coordinator.data["status"]["system_stats"]["sysload"] or 0, 2),
        subscriptions=[("system_stats", "sysload")],
        icon="mdi:cpu-64-bit",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MoonrakerSensorDescription(
        key="memused",
        name="Memory Used",
        value_fn=lambda sensor: calculate_memory_used(sensor.coordinator.data) or 0.0,
        subscriptions=[("system_stats", "memavail")],
        icon="mdi:memory",
        state_class=SensorStateClass.MEASUREMENT,
        unit=PERCENTAGE,
    ),
]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    descs: list[MoonrakerSensorDescription] = []
    await _collect_basic_sensors(coordinator, descs)
    await _collect_optional_sensors(coordinator, descs)
    await _collect_history_sensors(coordinator, descs)
    await _collect_queue_sensors(coordinator, descs)

    if not descs:
        return
    await coordinator.async_refresh()
    async_add_entities(MoonrakerSensor(coordinator, entry, d) for d in descs)


async def _machine_system_info_updater(coordinator):
    return {
        "system_info": (await coordinator.async_fetch_data(METHODS.MACHINE_SYSTEM_INFO))[
            "system_info"
        ]
    }


async def _collect_basic_sensors(coordinator, descs):
    coordinator.add_data_updater(_machine_system_info_updater)
    coordinator.load_sensor_data(SENSORS)
    descs.extend(SENSORS)


async def _collect_optional_sensors(coordinator, descs):
    """Set optional sensor platform."""
    temperature_keys = [
        "temperature_sensor",
        "temperature_fan",
        "temperature_probe",
        "tmc2240",
        "bme280",
        "htu21d",
        "lm75",
    ]
    fan_keys = ["heater_fan", "controller_fan", "fan_generic"]

    sensors: list[MoonrakerSensorDescription] = []
    object_list = await _get_object_list(coordinator)

    for obj in object_list.get("objects", []):
        split_obj = obj.split()

        if split_obj[0] in temperature_keys:
            status_key = obj
            name_base = split_obj[1].removesuffix("_temp").replace("_", " ").title()

            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{split_obj[0]}_{split_obj[1]}",
                    status_key=status_key,
                    name=f"{name_base} Temp",
                    value_fn=lambda sensor, sk=status_key: (
                        round(sensor.coordinator.data["status"][sk]["temperature"], 2)
                        if sensor.coordinator.data["status"][sk]["temperature"] is not None
                        else None
                    ),
                    subscriptions=[(obj, "temperature")],
                    icon="mdi:thermometer",
                    unit=UnitOfTemperature.CELSIUS,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

            if split_obj[0] == "bme280":
                result = await _get_config_for_obj(coordinator, obj, None)

                if "pressure" in result.get("status", {}).get(obj, {}):
                    sensors.append(
                        MoonrakerSensorDescription(
                            key=f"{split_obj[0]}_{split_obj[1]}_pressure",
                            status_key=status_key,
                            name=f"{split_obj[1].replace('_', ' ').title()} Pressure",
                            value_fn=lambda sensor, sk=status_key: sensor.coordinator.data["status"][sk][
                                "pressure"
                            ],
                            subscriptions=[(obj, "pressure")],
                            icon="mdi:gauge",
                            unit=UnitOfPressure.HPA,
                            state_class=SensorStateClass.MEASUREMENT,
                        )
                    )

                if "humidity" in result.get("status", {}).get(obj, {}):
                    sensors.append(
                        MoonrakerSensorDescription(
                            key=f"{split_obj[0]}_{split_obj[1]}_humidity",
                            status_key=status_key,
                            name=f"{split_obj[1].replace('_', ' ').title()} Humidity",
                            value_fn=lambda sensor, sk=status_key: sensor.coordinator.data["status"][sk][
                                "humidity"
                            ],
                            subscriptions=[(obj, "humidity")],
                            icon="mdi:water-percent",
                            unit=PERCENTAGE,
                            state_class=SensorStateClass.MEASUREMENT,
                        )
                    )

                if "gas" in result.get("status", {}).get(obj, {}):
                    sensors.append(
                        MoonrakerSensorDescription(
                            key=f"{split_obj[0]}_{split_obj[1]}_gas",
                            status_key=status_key,
                            name=f"{split_obj[1].replace('_', ' ').title()} Gas",
                            value_fn=lambda sensor, sk=status_key: sensor.coordinator.data["status"][sk]["gas"],
                            subscriptions=[(obj, "gas")],
                            icon="mdi:eye",
                            unit=None,
                            state_class=SensorStateClass.MEASUREMENT,
                        )
                    )

        elif split_obj[0] == "mcu":
            if len(split_obj) > 1:
                key = f"{split_obj[0]}_{split_obj[1]}"
                name = obj.replace("_", " ").title()
            else:
                key = split_obj[0]
                name = split_obj[0].title()
            status_key = obj

            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{key}_load",
                    status_key=status_key,
                    name=f"{name} Load",
                    value_fn=lambda sensor, sk=status_key: (
                        (
                            sensor.coordinator.data["status"][sk]["last_stats"]["mcu_task_avg"]
                            + 3 * sensor.coordinator.data["status"][sk]["last_stats"]["mcu_task_stddev"]
                        )
                        / 0.0025
                        * 100
                    )
                    if sensor.coordinator.data["status"][sk].get("last_stats") is not None
                    else 0,
                    subscriptions=[(obj, "last_stats")],
                    icon="mdi:cpu-64-bit",
                    state_class=SensorStateClass.MEASUREMENT,
                    unit=PERCENTAGE,
                )
            )

            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{key}_awake",
                    status_key=status_key,
                    name=f"{name} Awake",
                    value_fn=lambda sensor, sk=status_key: (
                        sensor.coordinator.data["status"][sk]["last_stats"]["mcu_awake"] / 5 * 100
                    )
                    if sensor.coordinator.data["status"][sk].get("last_stats") is not None
                    else 0,
                    icon="mdi:cpu-64-bit",
                    subscriptions=[(obj, "last_stats")],
                    state_class=SensorStateClass.MEASUREMENT,
                    unit=PERCENTAGE,
                )
            )

        elif split_obj[0] in fan_keys:
            status_key = obj
            pretty = split_obj[1].replace("_", " ").title()
            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{split_obj[0]}_{split_obj[1]}",
                    status_key=status_key,
                    name=pretty,
                    value_fn=lambda sensor, sk=status_key: sensor.coordinator.data["status"][sk]["speed"] * 100,
                    subscriptions=[(obj, "speed")],
                    icon="mdi:fan",
                    unit=PERCENTAGE,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

            fan_data = await _get_config_for_obj(coordinator, obj, ["rpm"])
            if fan_data.get("status", {}).get(obj, {}).get("rpm"):
                sensors.append(
                    MoonrakerSensorDescription(
                        key=f"{split_obj[0]}_{split_obj[1]}_rpm",
                        status_key=status_key,
                        name=f"{pretty} RPM",
                        value_fn=lambda sensor, sk=status_key: int(
                            sensor.coordinator.data["status"][sk]["rpm"]
                        )
                        if sensor.coordinator.data["status"][sk].get("rpm") is not None
                        else None,
                        subscriptions=[(obj, "rpm")],
                        icon="mdi:fan",
                        unit=REVOLUTIONS_PER_MINUTE,
                        state_class=SensorStateClass.MEASUREMENT,
                    )
                )

        elif obj == "fan":
            fan_data = await _get_config_for_obj(coordinator, "fan", ["rpm"])
            if fan_data.get("status", {}).get("fan", {}).get("rpm"):
                sensors.append(
                    MoonrakerSensorDescription(
                        key="fan_rpm",
                        name="Fan RPM",
                        value_fn=lambda sensor: int(sensor.coordinator.data["status"]["fan"]["rpm"])
                        if sensor.coordinator.data["status"]["fan"].get("rpm") is not None
                        else None,
                        subscriptions=[("fan", "rpm")],
                        icon="mdi:fan",
                        unit=REVOLUTIONS_PER_MINUTE,
                        state_class=SensorStateClass.MEASUREMENT,
                    )
                )

        elif split_obj[0] == "heater_generic":
            status_key = obj
            pretty = f"{split_obj[1].replace('_', ' ').title()}"

            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{split_obj[0]}_{split_obj[1]}_power",
                    status_key=status_key,
                    name=f"{pretty} Power",
                    value_fn=lambda sensor, sk=status_key: int(
                        (sensor.coordinator.data["status"][sk].get("power", 0.0) or 0.0) * 100
                    ),
                    subscriptions=[(obj, "power")],
                    icon="mdi:flash",
                    unit=PERCENTAGE,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{split_obj[0]}_{split_obj[1]}_temperature",
                    status_key=status_key,
                    name=f"{pretty} Temperature",
                    value_fn=lambda sensor, sk=status_key: (
                        round(sensor.coordinator.data["status"][sk]["temperature"], 2)
                        if sensor.coordinator.data["status"][sk].get("temperature") is not None
                        else None
                    ),
                    subscriptions=[(obj, "temperature")],
                    icon="mdi:thermometer",
                    unit=UnitOfTemperature.CELSIUS,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{split_obj[0]}_{split_obj[1]}_target",
                    status_key=status_key,
                    name=f"{pretty} Target",
                    value_fn=lambda sensor, sk=status_key: sensor.coordinator.data["status"][sk]["target"],
                    subscriptions=[(obj, "target")],
                    icon="mdi:radiator",
                    unit=UnitOfTemperature.CELSIUS,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

        elif obj.startswith("extruder") or obj.startswith("heater_bed"):
            if obj.startswith("extruder"):
                icon = "mdi:printer-3d-nozzle-heat"
                base_name = obj
                max_wattage = 60
            else:
                icon = "mdi:radiator"
                base_name = "Bed"
                max_wattage = 280

            status_key = obj
            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{obj}_temp",
                    status_key=status_key,
                    name=f"{base_name} Temperature".title(),
                    value_fn=lambda sensor, sk=status_key: (
                        round(sensor.coordinator.data["status"][sk]["temperature"], 2)
                        if sensor.coordinator.data["status"][sk].get("temperature") is not None
                        else None
                    ),
                    subscriptions=[(obj, "temperature")],
                    icon=icon,
                    unit=UnitOfTemperature.CELSIUS,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{obj}_power",
                    status_key=status_key,
                    name=f"{base_name} Power".title(),
                    value_fn=lambda sensor, sk=status_key: int(
                        (sensor.coordinator.data["status"][sk].get("power", 0.0) or 0.0) * 100
                    ),
                    subscriptions=[(obj, "power")],
                    icon="mdi:flash",
                    unit=PERCENTAGE,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

            sensors.append(
                MoonrakerSensorDescription(
                    key=f"{obj}_power_watts",
                    status_key=status_key,
                    name=f"{base_name} Power Watts".title(),
                    value_fn=lambda sensor, sk=status_key, wattage=max_wattage: round(
                        (sensor.coordinator.data["status"][sk].get("power", 0.0) or 0.0) * wattage, 2
                    ),
                    subscriptions=[(obj, "power")],
                    icon="mdi:lightning-bolt",
                    unit=UnitOfPower.WATT,
                    device_class=SensorDeviceClass.POWER,
                    state_class=SensorStateClass.MEASUREMENT,
                )
            )

    if sensors:
        coordinator.load_sensor_data(sensors)
        descs.extend(sensors)


async def _history_updater(coordinator):
    return {"history": await coordinator.async_fetch_data(METHODS.SERVER_HISTORY_TOTALS)}


async def _collect_history_sensors(coordinator, descs):
    try:
        history = await coordinator.async_fetch_data(METHODS.SERVER_HISTORY_TOTALS)
    except UpdateFailed as exc:
        _LOGGER.debug("Skipping history sensor discovery: %s", exc)
        return
    if history.get("error"):
        return

    coordinator.add_data_updater(_history_updater)

    sensors = [
        MoonrakerSensorDescription(
            key="total_jobs",
            name="Totals jobs",
            value_fn=lambda sensor: sensor.coordinator.data["history"]["job_totals"]["total_jobs"],
            subscriptions=[],
            icon="mdi:numeric",
            unit="Jobs",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        MoonrakerSensorDescription(
            key="total_print_time",
            name="Totals Print Time",
            value_fn=lambda sensor: convert_time(
                sensor.coordinator.data["history"]["job_totals"]["total_print_time"]
            ),
            subscriptions=[],
            icon="mdi:clock-outline",
        ),
        MoonrakerSensorDescription(
            key="total_filament_used",
            name="Totals Filament Used",
            value_fn=lambda sensor: round(
                sensor.coordinator.data["history"]["job_totals"]["total_filament_used"] / 1000,
                2,
            ),
            subscriptions=[],
            icon="mdi:clock-outline",
            unit=UnitOfLength.METERS,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        MoonrakerSensorDescription(
            key="longest_print",
            name="Longest Print",
            value_fn=lambda sensor: convert_time(
                sensor.coordinator.data["history"]["job_totals"]["longest_print"]
            ),
            subscriptions=[],
            icon="mdi:clock-outline",
        ),
    ]

    coordinator.load_sensor_data(sensors)
    descs.extend(sensors)


async def _queue_updater(coordinator):
    return {"queue": await coordinator.async_fetch_data(METHODS.SERVER_JOB_QUEUE_STATUS)}


async def _collect_queue_sensors(coordinator, descs):
    """Job queue sensors."""
    try:
        queue = await coordinator.async_fetch_data(METHODS.SERVER_JOB_QUEUE_STATUS)
    except UpdateFailed as exc:
        _LOGGER.debug("Skipping queue sensor discovery: %s", exc)
        return
    if queue.get("queue_state") is None or queue.get("queued_jobs") is None:
        return

    coordinator.add_data_updater(_queue_updater)

    sensors = [
        MoonrakerSensorDescription(
            key="queue_state",
            name="Queue State",
            value_fn=lambda sensor: sensor.coordinator.data["queue"]["queue_state"],
            subscriptions=[],  # updater-driven; don't subscribe
        ),
        MoonrakerSensorDescription(
            key="queue_count",
            name="Jobs in queue",
            value_fn=lambda sensor: len(sensor.coordinator.data["queue"]["queued_jobs"]),
            subscriptions=[],  # updater-driven; don't subscribe
            icon="mdi:numeric",
            unit="Jobs",
            state_class=SensorStateClass.MEASUREMENT,
        ),
    ]

    coordinator.load_sensor_data(sensors)
    descs.extend(sensors)


class MoonrakerSensor(BaseMoonrakerEntity, SensorEntity):
    """Moonraker sensor class."""

    def __init__(self, coordinator, entry, description):
        super().__init__(coordinator, entry)
        self.coordinator = coordinator
        self.status_key = description.status_key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self.entity_description = description
        try:
            self._attr_native_value = description.value_fn(self)
        except (KeyError, TypeError):
            self._attr_native_value = None
        self._attr_icon = description.icon
        self._attr_native_unit_of_measurement = description.unit

    @callback
    def _handle_coordinator_update(self) -> None:
        try:
            self._attr_native_value = self.entity_description.value_fn(self)
        except (KeyError, TypeError):
            self._attr_native_value = None
        self.async_write_ha_state()

    def empty_result_when_not_printing(self, value=""):
        if self.coordinator.data["status"]["print_stats"]["state"] != PRINTSTATES.PRINTING.value:
            return "" if isinstance(value, str) else 0.0
        return value


def calculate_pct_job(data) -> float:
    """
    Return print progress as a fraction 0.0–1.0.

    Priority:
      1) virtual_sdcard.progress (authoritative file progress)
      2) display_status.progress (fallback)
      3) filament_used / filament_total (last resort)

    We take the max of (1) and (2) to avoid under-reporting if one lags.
    """
    status = data.get("status", {})

    vsd = status.get("virtual_sdcard", {}).get("progress")
    dsp = status.get("display_status", {}).get("progress")

    progs = [p for p in (vsd, dsp) if isinstance(p, (int, float))]
    if progs:
        p = max(progs)  # prefer the higher of the two
        return max(0.0, min(1.0, float(p)))

    # Fallback: filament ratio
    expected_filament = data.get("filament_total") or 0
    filament_used = status.get("print_stats", {}).get("filament_used") or 0
    if expected_filament > 0 and filament_used >= 0:
        return max(0.0, min(1.0, filament_used / expected_filament))

    return 0.0

def calculate_eta(data):
    """Calculate ETA of current print."""
    percent_job = calculate_pct_job(data)
    if (
        data["status"]["print_stats"]["state"] != PRINTSTATES.PRINTING.value
        or data["status"]["print_stats"]["print_duration"] <= 0
        or percent_job <= 0.001
        or percent_job >= 1
    ):
        return None

    time_left = round(
        (data["status"]["print_stats"]["print_duration"] / percent_job)
        - data["status"]["print_stats"]["print_duration"],
        2,
    )
    return datetime.now(timezone.utc) + timedelta(0, time_left)


def calculate_current_layer(data):
    """Calculate current layer."""
    if (
        data["status"]["print_stats"]["state"] != PRINTSTATES.PRINTING.value
        or not data["status"]["print_stats"].get("filename")
    ):
        return 0

    if data["status"]["print_stats"].get("info") and data["status"]["print_stats"]["info"].get("current_layer") is not None:
        return data["status"]["print_stats"]["info"]["current_layer"]

    if not data.get("layer_height") or data["layer_height"] <= 0:
        return 0

    return int(
        round(
            (data["status"]["toolhead"]["position"][2] - data.get("first_layer_height", 0))
            / data["layer_height"],
            0,
        )
    ) + 1


def convert_time(time_s):
    """Convert seconds to 'Xh Ym Zs'."""
    return f"{round(time_s // 3600)}h {round(time_s % 3600 // 60)}m {round(time_s % 60)}s"


def calculate_memory_used(data):
    """Calculate memory used percent."""
    if not data.get("system_info") or not data["status"]["system_stats"].get("memavail"):
        return None

    total_memory = data["system_info"]["cpu_info"]["total_memory"]
    memory_used = total_memory - data["status"]["system_stats"]["memavail"]
    percent_mem_used = memory_used / total_memory * 100
    return round(percent_mem_used, 2)
