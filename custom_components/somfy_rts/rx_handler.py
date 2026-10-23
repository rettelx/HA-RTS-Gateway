"""RX handler for Somfy RTS frames received by ESPHome gateways.

The ESPHome ``somfy_rts`` external component decodes RTS frames on the
gateway and fires the HA event ``esphome.somfy_rts_rx`` with the frame
fields. A single global handler dispatches frames to every cover whose
address matches, including covers linked to a physical remote address.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    DOMAIN,
    EVENT_FRAME,
    RX_DUPLICATE_TTL,
    RX_EVENT,
    TX_LOOPBACK_TTL,
    SomfyCommand,
)
from .protocols import BUTTON_TO_COMMAND

_LOGGER = logging.getLogger(__name__)

# How long a remotely seen address stays discoverable in the UI
RECENT_FRAME_TTL_S = 120.0


class SomfyRTSRxHandler:
    """Dispatches received RTS frames to cover entities."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the handler."""
        self._hass = hass
        self._unsubscribe: list[Any] = []
        self._last_frames: dict[tuple[int, int], float] = {}
        self._press_counts: dict[tuple[int, int], int] = {}
        self._seen: dict[int, dict[str, Any]] = {}

    @callback
    def async_setup(self) -> None:
        """Listen for RTS frames from any ESPHome gateway."""
        self._unsubscribe.append(
            self._hass.bus.async_listen(RX_EVENT, self._handle_event)
        )
        _LOGGER.debug("Listening for Somfy RTS frames on event %s", RX_EVENT)

    @callback
    def recent_addresses(
        self, max_age_s: float = RECENT_FRAME_TTL_S
    ) -> list[dict[str, Any]]:
        """Return remote addresses recently heard on air.

        Used by the options flow to let the user pick a remote by pressing
        its buttons instead of typing its address. Gateway loopback frames
        are not tracked.
        """
        now = time.monotonic()
        self._seen = {
            address: info
            for address, info in self._seen.items()
            if now - info["time"] <= max_age_s
        }
        result = [
            {
                "address": address,
                "command": info["command"],
                "age": round(now - info["time"], 1),
            }
            for address, info in self._seen.items()
        ]
        result.sort(key=lambda item: item["age"])
        return result

    @callback
    def async_cleanup(self) -> None:
        """Stop listening."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()

    @callback
    def _dispatch(
        self,
        address: int,
        command: SomfyCommand | None,
        rolling_code: int,
        device: str | None,
        step: int,
        bits: int = 56,
        command_code: int = 0,
    ) -> None:
        """Forward a frame to all matching covers and fire an event."""
        now = time.monotonic()

        # Ignore loopback of our own transmissions (the gateway's CC1101
        # hears the frames Home Assistant sends).
        recent_tx = self._hass.data.get(DOMAIN, {}).get("recent_tx", {})
        sent_at = recent_tx.get((address, rolling_code))
        if sent_at is not None and now - sent_at < TX_LOOPBACK_TTL:
            self._fire_frame_event(
                address, command, rolling_code, device, step, bits,
                command_code, source="gateway",
            )
            return
        if len(recent_tx) > 256:
            for key, ts in list(recent_tx.items()):
                if now - ts >= TX_LOOPBACK_TTL:
                    del recent_tx[key]

        # Every RTS press is repeated on air several times with the same
        # rolling code. The first frame fires the event; the repeats are
        # only forwarded to the covers, with their count (long-press
        # detection on timed tilt motors).
        key = (address, rolling_code)
        last = self._last_frames.get(key)
        if last is not None and now - last < RX_DUPLICATE_TTL:
            repeat = self._press_counts.get(key, 1) + 1
            self._press_counts[key] = repeat
        else:
            self._last_frames[key] = now
            self._press_counts.pop(key, None)
            repeat = 1
            if len(self._last_frames) > 256:
                self._last_frames = {
                    k: t for k, t in self._last_frames.items() if now - t < RX_DUPLICATE_TTL
                }
                self._press_counts = {
                    k: c for k, c in self._press_counts.items() if k in self._last_frames
                }

            self._fire_frame_event(
                address, command, rolling_code, device, step, bits,
                command_code, source="remote",
            )
            self._seen[address] = {
                "time": now,
                "command": command.value if command is not None else "?",
            }

        matched = False
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if runtime is None:
                continue
            for entity in runtime.entities.values():
                if address in entity.linked_remote_addresses_int or (
                    address == entity._address_int
                ):
                    if command == SomfyCommand.SENSOR:
                        # A linked sun/wind sensor frame: update the alarm
                        # attributes of covers, not the position estimate.
                        update_sensor = getattr(
                            entity, "async_update_sensor_state", None
                        )
                        if update_sensor is not None:
                            update_sensor(rolling_code, device)
                    else:
                        entity.async_update_from_rx(
                            command, rolling_code, device, step, repeat
                        )
                    matched = True
            for sensor in runtime.sensors.values():
                if sensor._address_int == address and command == SomfyCommand.SENSOR:
                    sensor.async_update_from_rx(rolling_code, device)
                    matched = True
        if not matched:
            _LOGGER.debug(
                "RTS frame from unlinked address %d (command=%s)",
                address,
                command.value if command else "?",
            )

    def _fire_frame_event(
        self,
        address: int,
        command: SomfyCommand | None,
        rolling_code: int,
        device: str | None,
        step: int,
        bits: int,
        command_code: int,
        source: str,
    ) -> None:
        """Fire a somfy_rts_frame event on the HA event bus."""
        self._hass.bus.async_fire(
            EVENT_FRAME,
            {
                "address": address,
                "command": command.value if command is not None else None,
                "command_code": command_code,
                "rolling_code": rolling_code,
                "device": device,
                "step": step,
                "bits": bits,
                "source": source,
            },
        )

    @callback
    def _handle_event(self, event: Event) -> None:
        """Handle an incoming esphome.somfy_rts_rx event."""
        data = event.data
        try:
            address = int(str(data["address"]))
            command = int(str(data["command"]))
            rolling_code = int(str(data["rolling_code"]))
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Ignoring malformed RTS event %s: %s", data, err)
            return

        device = data.get("device")
        try:
            step = int(str(data.get("step", 0)))
        except (TypeError, ValueError):
            step = 0
        bits = int(str(data.get("bits", 56)))
        self._dispatch(
            address,
            BUTTON_TO_COMMAND.get(command),
            rolling_code,
            device,
            step,
            bits,
            command,
        )
