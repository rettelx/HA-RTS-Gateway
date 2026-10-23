"""Availability tracking for entities tied to the RF transmitter.

Every Somfy RTS entity talks to the radio through the transmitter entity
exposed by the ESPHome gateway. When that entity goes unavailable the
gateway is unreachable: the entities mark themselves unavailable and log
the transition once, in both directions (quality checklist items
entity-unavailable and log-when-unavailable).
"""

from __future__ import annotations

import logging

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def transmitter_available(hass: HomeAssistant, transmitter_id: str) -> bool:
    """Return whether the RF transmitter entity is available."""
    state = hass.states.get(transmitter_id)
    return state is not None and state.state not in (
        STATE_UNAVAILABLE,
        STATE_UNKNOWN,
    )


class TransmitterAvailabilityMixin:
    """Entity mixin: available while the transmitter entity is up."""

    _hass: HomeAssistant
    _transmitter_id: str = ""
    entity_id: str  # provided by the Entity base class
    _last_transmitter_available: bool | None = None

    @property
    def available(self) -> bool:
        """Return whether the RF transmitter is reachable."""
        is_available = transmitter_available(self._hass, self._transmitter_id)
        if (
            self._last_transmitter_available is not None
            and is_available != self._last_transmitter_available
        ):
            if is_available:
                _LOGGER.info(
                    "%s: RF transmitter is available again", self.entity_id
                )
            else:
                _LOGGER.warning(
                    "%s: RF transmitter is unavailable", self.entity_id
                )
        self._last_transmitter_available = is_available
        return is_available
