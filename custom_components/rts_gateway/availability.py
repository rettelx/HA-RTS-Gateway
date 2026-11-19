"""Availability tracking for entities tied to the RF transmitter.

Every RTS entity talks to the radio through the transmitter entity
exposed by the ESPHome gateway. When that entity goes unavailable the
gateway is unreachable: the entities mark themselves unavailable and log
the transition once, in both directions (quality checklist items
entity-unavailable and log-when-unavailable).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

_LOGGER = logging.getLogger(__name__)


def transmitter_available(hass: HomeAssistant, transmitter_id: str) -> bool:
    """Return whether the RF transmitter entity is available."""
    state = hass.states.get(transmitter_id)
    return state is not None and state.state not in (
        STATE_UNAVAILABLE,
        STATE_UNKNOWN,
    )


class TransmitterAvailabilityMixin(Entity):
    """Entity mixin: available while the transmitter entity is up.

    Availability is passive by default: the ``available`` property is
    only recomputed when the entity writes its state. Entities that
    never write their own state (buttons) would stay greyed out after
    a restart until an unrelated event -- so the mixin subscribes to
    the transmitter state changes and rewrites the entity state when
    they happen.
    """

    # Push-based entities: state is written on RX events and ticks,
    # HA must not poll them (quality scale: appropriate-polling).
    _attr_should_poll = False

    _hass: HomeAssistant
    _transmitter_id: str = ""
    _last_transmitter_available: bool | None = None
    _unsub_transmitter: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Return whether the entity and the RF transmitter are reachable."""
        return super().available and transmitter_available(
            self._hass, self._transmitter_id
        )

    async def async_added_to_hass(self) -> None:
        """Track the transmitter state so availability follows it live."""

        await super().async_added_to_hass()
        if not self._transmitter_id:
            return
        self._unsub_transmitter = async_track_state_change_event(
            self._hass,
            [self._transmitter_id],
            self._async_transmitter_changed,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Stop tracking the transmitter state."""

        if self._unsub_transmitter is not None:
            self._unsub_transmitter()
            self._unsub_transmitter = None
        await super().async_will_remove_from_hass()

    @callback
    def _async_transmitter_changed(self, event: Event[EventStateChangedData]) -> None:
        """Log the availability transition once and refresh the state.

        The ``available`` property recomputes on read; the state is
        rewritten only on the first event or when availability actually
        flips -- the only cases where its output changes -- which is
        what makes the UI update.
        """
        previous = self._last_transmitter_available
        is_available = transmitter_available(self._hass, self._transmitter_id)
        if previous is None or is_available != previous:
            if previous is not None:
                if is_available:
                    _LOGGER.info(
                        "%s: RF transmitter is available again", self.entity_id
                    )
                else:
                    _LOGGER.warning(
                        "%s: RF transmitter is unavailable", self.entity_id
                    )
            self.async_write_ha_state()
        self._last_transmitter_available = is_available
