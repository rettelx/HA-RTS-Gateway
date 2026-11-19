"""Tests for the radio_frequency helpers (rf.py)."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.rts_gateway import rf
from custom_components.rts_gateway.const import (
    PROG_MODE_REPEATS,
    SomfyCommand,
    SomfyProtocol,
)
from custom_components.rts_gateway.protocols import decode_timings

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


def test_send_command_draws_rolling_code_inside_lock(hass, native_rf, monkeypatch):
    """Sans rolling_code explicite, le code est tire DANS le lock par
    emetteur : deux envois concurrents recoivent des codes consecutifs
    du store (l'ordre d'antenne suit alors l'ordre des codes)."""
    import asyncio as asyncio_mod

    from custom_components.rts_gateway.const import DOMAIN
    from custom_components.rts_gateway.rolling_code import RollingCodeStore

    async def scenario():
        hass.data.setdefault(DOMAIN, {})["rolling_codes"] = RollingCodeStore(hass)
        monkeypatch.setattr(rf, "TX_GAP_S", 0.0)
        c1, c2 = await asyncio_mod.gather(
            rf.async_send_command(hass, TRANSMITTER, 424242, SomfyCommand.UP),
            rf.async_send_command(hass, TRANSMITTER, 424242, SomfyCommand.DOWN),
        )
        assert isinstance(c1, int) and isinstance(c2, int)
        assert {c1, c2} == {1, 2}  # consecutifs, tires du store
        assert abs(c1 - c2) == 1
        assert len(native_rf) == 2

    asyncio_mod.run(scenario())


def test_send_command_stamps_loopback_deadline_and_free_at(
    hass, native_rf, monkeypatch
):
    """rf.async_send_command stamps recent_tx with the absolute loopback
    deadline (air start + air time + margin) and free_at with air end +
    gap; a second send waits for the air to clear."""
    import time as time_mod

    from custom_components.rts_gateway.const import DOMAIN, TX_LOOPBACK_MARGIN_S

    async def scenario():
        monkeypatch.setattr(rf, "TX_GAP_S", 0.1)

        now = time_mod.monotonic()
        await rf.async_send_command(
            hass, TRANSMITTER, 0x123456, SomfyCommand.UP, 42
        )
        # air time of the burst that actually went out (async_send_command
        # uses _air_time_s(command_obj), not press_air_time_s)
        air_s = rf._air_time_s(native_rf[0][1])
        recent_tx = hass.data[DOMAIN]["recent_tx"]
        assert recent_tx[(0x123456, 42)] == pytest.approx(
            now + air_s + TX_LOOPBACK_MARGIN_S, abs=0.5
        )
        free_at = hass.data[DOMAIN]["tx_air"][f"free_at:{TRANSMITTER}"]
        assert free_at == pytest.approx(now + air_s + 0.1, abs=0.5)

        # Second send on the same transmitter waits for the air to clear.
        t0 = time_mod.monotonic()
        await rf.async_send_command(
            hass, TRANSMITTER, 0x123456, SomfyCommand.DOWN, 43
        )
        assert time_mod.monotonic() - t0 >= air_s - 0.2

    asyncio.run(scenario())


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
    # 80-bit plain MY decodes as MY (STOP is the 0xF extension).
    assert frame.command == SomfyCommand.MY


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


# Captured before the autouse no_press_hold fixture patches it.
_PRESS_AIR_TIME_S = rf.press_air_time_s


def test_press_air_time_s():
    """Duree sur les airs : timings bruts x (repeat_count + 1).

    rf_protocols compte les passes SUPPLEMENTAIRES (repeat_count) et
    le transmetteur ESPHome envoie le burst repeat_count + 1 fois au
    total ; le 80-bit (repeat_count=0) envoie une seule passe, dont les
    repetitions sont deja encodees dans les timings."""
    # 56-bit: SomfyRTSCommand a repeat_count=2 -> 3 passes du burst.
    assert round(
        _PRESS_AIR_TIME_S(
            SomfyCommand.PROG, SomfyProtocol.RTS_56BIT, PROG_MODE_REPEATS
        ),
        3,
    ) == 8.036
    # 80-bit: OOKCommand repeat_count=0 -> 1 passe, timings complets.
    assert round(
        _PRESS_AIR_TIME_S(
            SomfyCommand.PROG, SomfyProtocol.RTS_80BIT, PROG_MODE_REPEATS
        ),
        3,
    ) == 3.595
    assert round(
        _PRESS_AIR_TIME_S(SomfyCommand.PROG, SomfyProtocol.RTS_56BIT, 3), 3
    ) == 1.869
