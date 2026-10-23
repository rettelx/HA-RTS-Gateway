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
    """Return entity IDs of transmitters compatible with Somfy RTS.

    ``async_get_transmitters`` is a callback returning entity ID strings and
    raises ``HomeAssistantError`` when the component is missing or no
    transmitter exists.
    """
    try:
        return async_get_transmitters(hass, RTS_FREQUENCY_HZ, ModulationType.OOK)
    except HomeAssistantError as err:
        _LOGGER.debug("No compatible RF transmitter: %s", err)
        return []


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
    """Send a Somfy RTS command through a radio_frequency transmitter."""
    if command in (SomfyCommand.STEP_UP, SomfyCommand.STEP_DOWN):
        if protocol != SomfyProtocol.RTS_80BIT:
            raise ValueError(
                f"Command {command.value} requires the 80-bit protocol"
            )
        command_obj = build_80bit_command(
            address,
            command,
            rolling_code,
            step_size=step_size,
            frame_repeats=frame_repeats,
        )
    elif protocol == SomfyProtocol.RTS_80BIT:
        command_obj = build_80bit_command(
            address,
            command,
            rolling_code,
            frame_repeats=frame_repeats,
        )
    else:
        command_obj = build_command(address, command, rolling_code, frame_repeats)
    await rf_async_send_command(hass, transmitter_id, command_obj)
