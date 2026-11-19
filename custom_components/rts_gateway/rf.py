"""Helpers for the native radio_frequency integration."""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.components.radio_frequency import (
    ModulationType,
    async_get_transmitters,
)
from homeassistant.components.radio_frequency import (
    async_send_command as rf_async_send_command,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from rf_protocols import RadioFrequencyCommand

from .const import (
    DEFAULT_FRAME_REPEATS,
    DEFAULT_STEP_SIZE,
    DOMAIN,
    RTS_FREQUENCY_HZ,
    TX_LOOPBACK_MARGIN_S,
    SomfyCommand,
    SomfyProtocol,
)
from .protocols import build_80bit_command, build_command
from .rolling_code import RollingCodeStore

_LOGGER = logging.getLogger(__name__)

# Margin added after a burst's computed air time before the transmitter is
# considered free for the next command.
TX_GAP_S = 0.2


@callback
def get_transmitters(hass: HomeAssistant) -> list[str]:
    """Return entity IDs of transmitters compatible with RTS.

    ``async_get_transmitters`` is a callback returning entity ID strings and
    raises ``HomeAssistantError`` when the component is missing or no
    transmitter exists.
    """
    try:
        return async_get_transmitters(hass, RTS_FREQUENCY_HZ, ModulationType.OOK)
    except HomeAssistantError as err:
        _LOGGER.debug("No compatible RF transmitter: %s", err)
        return []


def _build_command(
    address: int,
    command: SomfyCommand,
    rolling_code: int,
    protocol: SomfyProtocol,
    frame_repeats: int,
    step_size: int,
) -> RadioFrequencyCommand:
    """Build the radio command object of a press."""
    if command in (SomfyCommand.STEP_UP, SomfyCommand.STEP_DOWN):
        if protocol != SomfyProtocol.RTS_80BIT:
            raise ValueError(
                f"Command {command.value} requires the 80-bit protocol"
            )
        return build_80bit_command(
            address,
            command,
            rolling_code,
            step_size=step_size,
            frame_repeats=frame_repeats,
        )
    if protocol == SomfyProtocol.RTS_80BIT:
        return build_80bit_command(
            address,
            command,
            rolling_code,
            frame_repeats=frame_repeats,
        )
    return build_command(address, command, rolling_code, frame_repeats)


def _air_time_s(command_obj: RadioFrequencyCommand) -> float:
    """Total on-air duration of a command object, in seconds.

    ``repeat_count`` in rf_protocols is the number of ADDITIONAL times
    the raw timings are sent; the ESPHome transmitter sends the burst
    ``repeat_count + 1`` times (homeassistant/components/esphome/
    radio_frequency.py), so the multiplier is ``repeat_count + 1``."""
    air_time_us = int(sum(abs(timing) for timing in command_obj.get_raw_timings()))
    return float(air_time_us * (int(command_obj.repeat_count) + 1) / 1e6)


@callback
def press_air_time_s(
    command: SomfyCommand,
    protocol: SomfyProtocol,
    frame_repeats: int,
    step_size: int = DEFAULT_STEP_SIZE,
) -> float:
    """Return the on-air duration of a press, in seconds.

    The gateway transmits non-blocking: a press issued while another
    one is still on air queues behind it. The wizard uses this to
    wait a long PROG press out before showing the step whose
    validation sends the next press.
    """
    return _air_time_s(
        _build_command(0, command, 0, protocol, frame_repeats, step_size)
    )


async def _async_next_rolling_code(hass: HomeAssistant, address: int) -> int:
    """Draw the next rolling code for an address from the shared store."""

    store = hass.data.get(DOMAIN, {}).get("rolling_codes")
    if store is None:
        store = RollingCodeStore(hass)
        hass.data.setdefault(DOMAIN, {})["rolling_codes"] = store
    return int(await store.async_get_next(address))


async def async_send_command(
    hass: HomeAssistant,
    transmitter_id: str,
    address: int,
    command: SomfyCommand,
    rolling_code: int | None = None,
    protocol: SomfyProtocol = SomfyProtocol.RTS_56BIT,
    frame_repeats: int = DEFAULT_FRAME_REPEATS,
    step_size: int = DEFAULT_STEP_SIZE,
) -> int:
    """Send a RTS command through a radio_frequency transmitter.

    The ESPHome transmitter is non-blocking: a command issued while a
    burst is still on air queues behind it and only starts much later.
    That delay used to break two things at once: the loopback filter
    (``recent_tx`` was stamped by the callers when this returned, so a
    late-aired burst's echo arrived after the loopback window and was misread
    as a remote press, clobbering the movement target) and the position
    estimator (started at queue time, not at air time, so the stop frame
    went out too early). Serialize transmissions per transmitter and wait
    for the previous burst to clear the air before queueing the next one,
    so this function returns when the burst actually starts on air.

    The loopback filter is stamped here, inside the lock and right after
    the burst is queued on air, so every caller gets the same guarantee:
    the gateway's echo of this frame is never mistaken for a remote press.

    When ``rolling_code`` is None, the next code is drawn from the shared
    rolling code store INSIDE the lock, after the wait for the previous
    burst: the air order then always matches the rolling-code order (a
    lower code aired after a higher one would be dropped by the motor's
    anti-replay window). Returns the rolling code used.
    """
    tx_state = hass.data.setdefault(DOMAIN, {}).setdefault("tx_air", {})
    lock_key = f"lock:{transmitter_id}"
    lock = tx_state.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        tx_state[lock_key] = lock
    async with lock:
        wait = tx_state.get(f"free_at:{transmitter_id}", 0.0) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        if rolling_code is None:
            rolling_code = await _async_next_rolling_code(hass, address)
        command_obj = _build_command(
            address, command, rolling_code, protocol, frame_repeats, step_size
        )
        air_s = _air_time_s(command_obj)
        await rf_async_send_command(hass, transmitter_id, command_obj)
        now = time.monotonic()
        tx_state[f"free_at:{transmitter_id}"] = now + air_s + TX_GAP_S
        recent_tx = hass.data.setdefault(DOMAIN, {}).setdefault("recent_tx", {})
        recent_tx[(address, rolling_code)] = now + air_s + TX_LOOPBACK_MARGIN_S
        if len(recent_tx) > 256:
            for key, until in list(recent_tx.items()):
                if now >= until:
                    del recent_tx[key]
    return rolling_code
