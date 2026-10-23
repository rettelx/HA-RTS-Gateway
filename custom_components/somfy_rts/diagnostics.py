"""Diagnostics support for the Somfy RTS integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_TRANSMITTER_ID, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Somfy RTS config entry.

    Includes the device configuration (addresses, travel times, options)
    and the last rolling codes: this is the data needed to debug radio
    issues, so nothing is redacted.
    """
    data: dict[str, Any] = {
        "version": "2.0.0",
        "transmitter": entry.data.get(CONF_TRANSMITTER_ID),
        "options": dict(entry.options),
        "devices": [dict(d) for d in entry.data.get("devices", [])],
        "sensors": [dict(s) for s in entry.data.get("sensors", [])],
    }
    store = hass.data.get(DOMAIN, {}).get("rolling_codes")
    codes: dict[str, Any] = {}
    if store is not None:
        raw = getattr(store, "_data", None)
        if isinstance(raw, dict):
            codes = {
                str(k): dict(v)
                for k, v in (raw.get("devices", {}) or {}).items()
            }
    data["rolling_codes"] = codes
    entities: dict[str, str] = {}
    runtime = getattr(entry, "runtime_data", None)
    for entity_id, entity in (
        (runtime.entities if runtime is not None else {}).items()
    ):
        entities[entity_id] = type(entity).__name__
    data["entities"] = entities
    return data
