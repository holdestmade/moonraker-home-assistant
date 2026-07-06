"""Shared discovery helpers for Moonraker platform setup.

These fetch-and-cache helpers are used while platforms enumerate the
printer's objects. Results are cached in ``coordinator.data`` so multiple
platforms setting up in the same cycle don't re-query Moonraker; the cache
naturally disappears on the next coordinator refresh (data is replaced).
"""

from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import METHODS, OBJ


async def get_object_list(coordinator) -> dict:
    """Fetch and cache printer.objects.list (guarantees {'objects': [...]})."""
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


async def get_query_cached(
    coordinator, obj: str, fields: list[str] | None = None
) -> dict:
    """Query one printer object once per setup pass; cache by object+fields."""
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


async def get_config_settings(coordinator) -> dict:
    """Query and cache the printer's configfile settings."""
    return await get_query_cached(coordinator, "configfile", ["settings"])


def is_output_pin(obj: str) -> bool:
    """True iff *obj* is an `output_pin <name>` entry."""
    parts = obj.split(" ", 1)
    return len(parts) == 2 and parts[0] == "output_pin"
