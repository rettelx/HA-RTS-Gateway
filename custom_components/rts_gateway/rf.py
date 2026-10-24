"""Helpers for the native radio_frequency integration."""

from __future__ import annotations

import logging

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
    RTS_FREQUENCY_HZ,
    SomfyCommand,
    SomfyProtocol,
)
from .protocols import build_80bit_command, build_command

_LOGGER = logging.getLogger(__name__)


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
    command_obj = _build_command(0, command, 0, protocol, frame_repeats, step_size)
    air_time_us = int(
        sum(abs(timing) for timing in command_obj.get_raw_timings())
    )
    repeat_count = max(int(command_obj.repeat_count), 1)
    return air_time_us * repeat_count / 1e6


async def async_send_command(
    hass: HomeAssistant,
    transmitter_id: str,
    address: int,
    command: SomfyCommand,
    rolling_code: int,
    protocol: SomfyProtocol = SomfyProtocol.RTS_56BIT,
    frame_repeats: int = DEFAULT_FRAME_REPEATS,
    step_size: int = DEFAULT_STEP_SIZE,
) -> None:
    """Send a RTS command through a radio_frequency transmitter."""
    command_obj = _build_command(
        address, command, rolling_code, protocol, frame_repeats, step_size
    )
    await rf_async_send_command(hass, transmitter_id, command_obj)
