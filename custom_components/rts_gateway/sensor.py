"""Sensor platform exposing each cover's My (favorite) position.

The My position is stored per cover in the config entry; this sensor
mirrors it on the device page so it is readable at a glance. The value
is read live from the cover entity, which notifies the sensor whenever
the position changes (options flow, set_my_position service, slider,
long MY press received from a physical remote).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_MY_POSITION,
    CONF_SIMULATE_MY,
    COVER_DEVICE_TYPES,
    DOMAIN,
    SomfyDeviceType,
)

# Radio actions are serialized per entity to avoid overlapping bursts.
PARALLEL_UPDATES = 1
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one My-position sensor per cover device."""

    entities = [
        SomfyRTSMyPositionSensor(hass, entry, device)
        for device in entry.data.get("devices", [])
        if SomfyDeviceType(device.get(CONF_DEVICE_TYPE)) in COVER_DEVICE_TYPES
    ]
    async_add_entities(entities)


def find_cover_entity(
    hass: HomeAssistant, entry: ConfigEntry, address: str
) -> Any | None:
    """Find the cover entity of a device address in the runtime registry."""

    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return None
    for entity in runtime.entities.values():
        if getattr(entity, "_address", None) == address and hasattr(
            entity, "async_goto_my_position"
        ):
            return entity
    return None


def config_my_position(entry: ConfigEntry, address: str) -> int | None:
    """My position stored in the config entry for a device address."""

    for device in entry.data.get(CONF_DEVICES, []):
        if str(device.get(CONF_ADDRESS)) == address:
            value = device.get(CONF_MY_POSITION)
            return int(value) if value is not None else None
    return None


class SomfyRTSMyPositionSensor(SensorEntity):
    """My (favorite) position of a cover, in percent (100 = fully open).

    Pure config value: no radio needed, so the sensor stays available
    even while the gateway transmitter is offline.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "my_position"
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""

        self._hass = hass
        self._entry = entry
        self._device = dict(device)
        self._address = str(device.get(CONF_ADDRESS, ""))
        # Read the instance property: on HA 2026.10+ the class-level
        # _attr_translation_key is a property object, whose repr embeds
        # a memory address -- unique_ids built from it changed at every
        # restart (see button.py).
        self._attr_unique_id = f"{DOMAIN}_{self._address}_{self.translation_key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self._address)})

    def _cover(self) -> Any | None:
        return find_cover_entity(self._hass, self._entry, self._address)

    @property
    def native_value(self) -> float | None:
        """Current My position in percent, None when not configured."""

        cover = self._cover()
        if cover is not None and cover._my_position is not None:
            return float(cover._my_position)
        # Cover not loaded (yet) or value unset there: live config value.
        my_position = config_my_position(self._entry, self._address)
        if my_position is not None:
            return float(my_position)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes."""

        cover = self._cover()
        if cover is not None:
            simulate_my = bool(cover._simulate_my)
        else:
            simulate_my = bool(self._device.get(CONF_SIMULATE_MY, True))
        return {
            "address": self._address,
            "simulate_my": simulate_my,
        }

    async def async_added_to_hass(self) -> None:
        """Register the sensor so the cover can refresh it on change."""

        await super().async_added_to_hass()
        self._entry.runtime_data.my_sensors[self._address] = self

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the sensor."""

        self._entry.runtime_data.my_sensors.pop(self._address, None)
        await super().async_will_remove_from_hass()
