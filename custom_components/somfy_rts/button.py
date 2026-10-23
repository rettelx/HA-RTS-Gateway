"""Button platform for Somfy RTS devices.

Adds device-page actions: go to the favorite (My) position, recalibrate
the position estimate with a full close/open cycle, and the PROG
pairing/unpairing frame (the same radio command toggles the pairing on
the motor, the same radio command toggling the pairing).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .availability import TransmitterAvailabilityMixin
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_TYPE,
    CONF_TRANSMITTER_ID,
    COVER_DEVICE_TYPES,
    DOMAIN,
    SomfyDeviceType,
)
from .cover import SomfyRTSCover

# Radio actions are serialized per entity to avoid overlapping bursts.
PARALLEL_UPDATES = 1
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Somfy RTS buttons from a config entry."""
    entities: list[ButtonEntity] = []
    for device in entry.data.get("devices", []):
        device_type = SomfyDeviceType(device.get(CONF_DEVICE_TYPE))
        if device_type in COVER_DEVICE_TYPES:
            entities.append(SomfyRTSGotoMyButton(hass, entry, device))
            entities.append(SomfyRTSRecalibrateButton(hass, entry, device))
    async_add_entities(entities)


def _find_cover_entity(
    hass: HomeAssistant, entry: ConfigEntry, address: str
) -> SomfyRTSCover | None:
    """Find the cover entity of a device address in the entity registry."""
    for entity in entry.runtime_data.entities.values():
        if getattr(entity, "_address", None) == address and hasattr(
            entity, "async_goto_my_position"
        ):
            cover: SomfyRTSCover = entity
            return cover
    return None


class SomfyRTSButtonBase(TransmitterAvailabilityMixin, ButtonEntity):
    """Base for Somfy RTS buttons attached to a device."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, device: dict[str, Any]
    ) -> None:
        """Initialize the button."""
        self._hass = hass
        self._entry = entry
        self._device = dict(device)
        self._address = str(device.get(CONF_ADDRESS, ""))
        self._address_int = int(self._address)
        self._transmitter_id = str(
            self._entry.data.get(CONF_TRANSMITTER_ID, "")
        )
        self._attr_has_entity_name = True
        key = type(self)._attr_translation_key
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._address)})

class SomfyRTSGotoMyButton(SomfyRTSButtonBase):
    """Move the cover to its configured My (favorite) position."""

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_translation_key = "goto_my_position"

    async def async_press(self) -> None:
        """Go to the favorite position."""
        entity = _find_cover_entity(self._hass, self._entry, self._address)
        if entity is None:
            _LOGGER.warning("No cover entity found for %s", self._address)
            return
        if getattr(entity, "_my_position", None) is None:
            _LOGGER.warning(
                "No My position configured for %s", self._address
            )
            return
        await entity.async_goto_my_position()


class SomfyRTSRecalibrateButton(SomfyRTSButtonBase):
    """Re-anchor the position estimate with a full close/open cycle."""

    _attr_translation_key = "recalibrate"

    async def async_press(self) -> None:
        """Run the recalibration cycle."""
        entity = _find_cover_entity(self._hass, self._entry, self._address)
        if entity is None:
            _LOGGER.warning("No cover entity found for %s", self._address)
            return
        entity.async_recalibrate()
