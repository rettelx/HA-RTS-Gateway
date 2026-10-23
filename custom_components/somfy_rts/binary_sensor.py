"""Binary sensor platform for Somfy RTS sun/wind sensors.

Somfy RTS sensors (Sunis Wirefree, Eolis, Soliris) send frames with the
Sensor command (0xE). Each configured sensor address exposes two entities:
a wind alarm and a sun alarm.

The sensor state lives in the 16-bit field that normally carries the
rolling code (bit 0 = wind, bit 1 = sun). The raw
value is exposed as an attribute so the mapping can be verified against a
physical sensor.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .availability import TransmitterAvailabilityMixin
from .const import (
    CONF_ADDRESS,
    CONF_NAME,
    CONF_SENSORS,
    CONF_TRANSMITTER_ID,
    DOMAIN,
    SENSOR_MODEL_LABELS,
    SENSOR_SUN_BIT,
    SENSOR_WIND_BIT,
    localized_label,
)

# Radio actions are serialized per entity to avoid overlapping bursts.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)

KIND_WIND = "wind"
KIND_SUN = "sun"


def parse_sensor_state(rolling_code: int) -> tuple[bool, bool]:
    """Return (wind, sun) from a sensor frame payload."""
    return bool(rolling_code & SENSOR_WIND_BIT), bool(rolling_code & SENSOR_SUN_BIT)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Somfy RTS sensor entities from a config entry."""
    sensors = entry.data.get(CONF_SENSORS, [])
    entities = [
        SomfyRTSSensor(hass, entry, sensor, kind)
        for sensor in sensors
        for kind in (KIND_WIND, KIND_SUN)
    ]
    async_add_entities(entities)


class SomfyRTSSensor(TransmitterAvailabilityMixin, BinarySensorEntity):
    """Representation of a Somfy RTS sun/wind alarm."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        sensor_config: dict[str, Any],
        kind: str,
    ) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._entry = entry
        self._kind = kind

        self._address = str(sensor_config[CONF_ADDRESS])
        self._address_int = int(self._address)
        self._name = sensor_config.get(CONF_NAME, f"Somfy RTS Sensor {self._address}")
        self._transmitter_id = sensor_config.get(CONF_TRANSMITTER_ID, "")

        self._attr_unique_id = f"{DOMAIN}_{self._address}_{kind}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"sensor_{self._address}")},
            name=self._name,
            manufacturer="Somfy",
            model=localized_label(self._hass, SENSOR_MODEL_LABELS, "RTS sun/wind sensor"),
            via_device=(DOMAIN, sensor_config.get(CONF_TRANSMITTER_ID, "")),
        )
        self._attr_translation_key = kind
        self._attr_is_on = False
        self._last_payload: int | None = None
        self._last_rx_device: str | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes."""
        return {
            "address": self._address,
            "last_payload": self._last_payload,
            "last_rx_device": self._last_rx_device,
        }

    async def async_added_to_hass(self) -> None:
        """Register the entity."""
        registry = self._entry.runtime_data.sensors
        registry[(self._address_int, self._kind)] = self

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the entity."""
        registry = self._entry.runtime_data.sensors
        registry.pop((self._address_int, self._kind), None)

    def async_update_from_rx(self, payload: int, device: str | None) -> None:
        """Update the alarm from a received sensor frame."""
        wind, sun = parse_sensor_state(payload)
        state = wind if self._kind == KIND_WIND else sun
        if state != self._attr_is_on or self._last_payload != payload:
            self._attr_is_on = state
        self._last_payload = payload
        self._last_rx_device = device
        self.async_write_ha_state()
