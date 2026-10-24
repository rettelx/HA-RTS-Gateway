"""Persistent rolling code storage for RTS virtual remotes.

A RTS receiver only accepts rolling codes close to the last one it
saw (window of a few hundred codes). Losing the counter would desynchronize
the virtual remote and require re-pairing every motor, so counters are
persisted in HA storage and incremented on every transmission.

Addresses are unique across config entries, so a single store keyed by
address is used.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "rts_gateway.rolling_codes"
DEFAULT_STARTING_COUNTER = 1


class RollingCodeStore:
    """Persistent per-address rolling code store."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._hass = hass
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] | None = None

    async def _async_load(self) -> dict[str, Any]:
        """Load storage data once."""
        if self._data is None:
            stored = await self._store.async_load()
            self._data = stored if isinstance(stored, dict) else {"devices": {}}
            self._data.setdefault("devices", {})
        return self._data

    async def async_get(self, address: int) -> int:
        """Get the next rolling code for an address without consuming it."""
        data = await self._async_load()
        device = data["devices"].get(str(address), {})
        return int(device.get("counter", DEFAULT_STARTING_COUNTER))

    async def async_get_next(self, address: int) -> int:
        """Consume and persist the next rolling code for an address."""
        data = await self._async_load()
        counter = int(
            data["devices"].get(str(address), {}).get(
                "counter", DEFAULT_STARTING_COUNTER
            )
        )
        data["devices"][str(address)] = {"counter": counter + 1}
        await self._store.async_save(data)
        _LOGGER.debug("Rolling code %d consumed for address %d", counter, address)
        return counter

    async def async_set(self, address: int, counter: int) -> None:
        """Force the counter for an address (e.g. after re-pairing)."""
        data = await self._async_load()
        data["devices"][str(address)] = {"counter": counter}
        await self._store.async_save(data)

    async def async_remove(self, address: int) -> None:
        """Forget an address."""
        data = await self._async_load()
        data["devices"].pop(str(address), None)
        await self._store.async_save(data)
