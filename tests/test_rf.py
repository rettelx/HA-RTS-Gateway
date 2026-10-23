"""Tests for the radio_frequency helpers (rf.py)."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.somfy_rts import rf
from custom_components.somfy_rts.const import SomfyCommand, SomfyProtocol
from custom_components.somfy_rts.protocols import decode_timings

TRANSMITTER = "radio_frequency.test_radio"


@pytest.fixture
def native_rf(monkeypatch):
    """Capture the command object handed to the native integration."""
    sent = []

    async def fake_send(hass, transmitter_id, command):
        sent.append((transmitter_id, command))

    monkeypatch.setattr(rf, "rf_async_send_command", fake_send)
    return sent


def test_get_transmitters_passthrough(hass, monkeypatch):
    monkeypatch.setattr(
        rf, "async_get_transmitters", lambda hass, freq, mod: [TRANSMITTER]
    )
    assert rf.get_transmitters(hass) == [TRANSMITTER]


def test_get_transmitters_swallows_errors(hass, monkeypatch):
    def raise_missing(hass, freq, mod):
        raise HomeAssistantError("radio_frequency not set up")

    monkeypatch.setattr(rf, "async_get_transmitters", raise_missing)
    assert rf.get_transmitters(hass) == []


def test_send_56bit_command(hass, native_rf):
    async def scenario():
        await rf.async_send_command(
            hass, TRANSMITTER, 0x123456, SomfyCommand.UP, 42
        )

    asyncio.run(scenario())
    assert len(native_rf) == 1
    transmitter_id, command = native_rf[0]
    assert transmitter_id == TRANSMITTER
    frame = decode_timings(command.get_raw_timings())
    assert frame is not None
    assert frame.address == 0x123456
    assert frame.command == SomfyCommand.UP
    assert frame.rolling_code == 42
    assert frame.bits == 56


def test_send_80bit_command(hass, native_rf):
    async def scenario():
        await rf.async_send_command(
            hass,
            TRANSMITTER,
            0x654321,
            SomfyCommand.MY,
            7,
            protocol=SomfyProtocol.RTS_80BIT,
        )

    asyncio.run(scenario())
    _, command = native_rf[0]
    frame = decode_timings(command.get_raw_timings())
    assert frame is not None
    assert frame.bits == 80
    assert frame.address == 0x654321
    assert frame.command == SomfyCommand.STOP


def test_step_command_forwards_step_size(hass, native_rf):
    async def scenario():
        await rf.async_send_command(
            hass,
            TRANSMITTER,
            0x222222,
            SomfyCommand.STEP_UP,
            9,
            protocol=SomfyProtocol.RTS_80BIT,
            step_size=100,
        )

    asyncio.run(scenario())
    _, command = native_rf[0]
    frame = decode_timings(command.get_raw_timings())
    assert frame is not None
    assert frame.command == SomfyCommand.STEP_UP
    assert frame.step == 100


def test_step_command_requires_80bit(hass, native_rf):
    async def scenario():
        await rf.async_send_command(
            hass,
            TRANSMITTER,
            0x123,
            SomfyCommand.STEP_UP,
            9,
            protocol=SomfyProtocol.RTS_56BIT,
        )

    with pytest.raises(ValueError):
        asyncio.run(scenario())
    assert native_rf == []


def test_step_size_is_limited_to_7_bits(hass, native_rf):
    """Regression: the 80-bit frame only carries 7 step bits (0-127)."""
    async def send(step_size):
        await rf.async_send_command(
            hass,
            TRANSMITTER,
            0x123,
            SomfyCommand.STEP_UP,
            9,
            protocol=SomfyProtocol.RTS_80BIT,
            step_size=step_size,
        )

    asyncio.run(send(127))  # ok
    with pytest.raises(ValueError):
        asyncio.run(send(128))
