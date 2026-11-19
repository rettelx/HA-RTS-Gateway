"""Number platform: slider editing a cover's My (favorite) position.

Only created for covers whose My position is simulated by Home Assistant
(``simulate_my``): the favorite then lives in the config entry, so the
slider edits it directly and persists it. For native motors the
favorite is stored in the motor itself (long MY press on the remote);
the My-position sensor still shows the last known value there.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_TYPE,
    CONF_SIMULATE_MY,
    COVER_DEVICE_TYPES,
    DOMAIN,
    SomfyDeviceType,
)
from .sensor import config_my_position, find_cover_entity

# Radio actions are serialized per entity to avoid overlapping bursts.
PARALLEL_UPDATES = 1
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the My-position slider for simulated-My cover devices."""

    entities = [
        SomfyRTSMyPositionNumber(hass, entry, device)
        for device in entry.data.get("devices", [])
        if SomfyDeviceType(device.get(CONF_DEVICE_TYPE)) in COVER_DEVICE_TYPES
        and bool(device.get(CONF_SIMULATE_MY, True))
    ]
    async_add_entities(entities)


class SomfyRTSMyPositionNumber(NumberEntity):
    """Slider editing the simulated My (favorite) position of a cover.

    Pure config value: no radio needed, so the slider stays available
    even while the gateway transmitter is offline.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False  # pure config value, pushed by the cover
    _attr_translation_key = "set_my_position"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device: dict[str, Any],
    ) -> None:
        """Initialize the slider."""

        self._hass = hass
        self._entry = entry
        self._device = dict(device)
        self._address = str(device.get(CONF_ADDRESS, ""))
        # Instance property, see sensor.py / button.py.
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self.translation_key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._address)})

    async def async_added_to_hass(self) -> None:
        """Register the slider so the cover can refresh it on change."""
        await super().async_added_to_hass()
        self._entry.runtime_data.my_numbers[self._address] = self

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the slider."""
        self._entry.runtime_data.my_numbers.pop(self._address, None)
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> float | None:
        """Current My position in percent, None when not configured."""

        cover = find_cover_entity(self._hass, self._entry, self._address)
        if cover is not None and cover._my_position is not None:
            return float(cover._my_position)
        my_position = config_my_position(self._entry, self._address)
        if my_position is not None:
            return float(my_position)
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Store the new My position on the cover and the config entry."""

        cover = find_cover_entity(self._hass, self._entry, self._address)
        if cover is None:
            _LOGGER.warning("No cover entity found for %s", self._address)
            return
        cover.async_set_my_position_persisted(int(value))
        self.async_write_ha_state()
