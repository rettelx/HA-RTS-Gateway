"""Custom integration for Somfy RTS covers.

Sends Somfy RTS commands through the native ``radio_frequency``
integration (an ESPHome gateway with a CC1101 exposes a transmitter
entity) and updates cover state from RTS frames decoded by the ESPHome
``somfy_rts`` component.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from . import rf
from .const import (
    CONF_ENABLE_RX,
    CONF_TRANSMITTER_ID,
    DEFAULT_ENABLE_RX,
    DOMAIN,
    PLATFORMS,
)
from .rolling_code import RollingCodeStore
from .rx_handler import SomfyRTSRxHandler
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)


@dataclass
class SomfyRTSRuntimeData:
    """Runtime state of a loaded Somfy RTS config entry."""

    entities: dict[str, Any] = field(default_factory=dict)
    sensors: dict[Any, Any] = field(default_factory=dict)


def _rx_enabled(entry: ConfigEntry) -> bool:
    """Return whether RX is enabled for an entry (options override data)."""
    return bool(
        entry.options.get(
            CONF_ENABLE_RX, entry.data.get(CONF_ENABLE_RX, DEFAULT_ENABLE_RX)
        )
    )


def _ensure_rx_handler(hass: HomeAssistant) -> None:
    """Start the global RX handler if not already running."""
    if "rx_handler" in hass.data[DOMAIN]:
        return
    handler = SomfyRTSRxHandler(hass)
    handler.async_setup()
    hass.data[DOMAIN]["rx_handler"] = handler


async def async_setup(hass: HomeAssistant, config: dict[str, Any] | None) -> bool:
    """Set up the Somfy RTS domain."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("rolling_codes", RollingCodeStore(hass))
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Somfy RTS from a config entry."""
    if DOMAIN not in hass.data or "rolling_codes" not in hass.data[DOMAIN]:
        # async_setup runs before the first entry, but stay defensive.
        await async_setup(hass, None)
    else:
        # async_setup runs once per HA session: re-register the services
        # in case a previous unload of the last entry removed them.
        await async_setup_services(hass)

    if entry.data.get(CONF_TRANSMITTER_ID) not in rf.get_transmitters(hass):
        raise ConfigEntryNotReady("RF transmitter not available yet")

    # Per-entry runtime data lives on the entry itself; Home Assistant
    # clears it automatically when the entry is unloaded.
    entry.runtime_data = SomfyRTSRuntimeData()
    hass.data[DOMAIN].setdefault("loaded_entries", set()).add(
        entry.entry_id
    )

    if _rx_enabled(entry):
        _ensure_rx_handler(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Somfy RTS config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    hass.data[DOMAIN].get("loaded_entries", set()).discard(entry.entry_id)

    # Stop the RX handler and services when no loaded entry remains.
    # The entry being unloaded (or removed) is still returned by
    # async_entries during this call, so loaded entries are told
    # apart by their data in hass.data.
    loaded = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id in hass.data[DOMAIN].get("loaded_entries", set())
    ]
    if not any(_rx_enabled(e) for e in loaded):
        handler = hass.data[DOMAIN].pop("rx_handler", None)
        if handler is not None:
            handler.async_cleanup()

    if not loaded:
        await async_unload_services(hass)

    return True
