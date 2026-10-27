"""Tests for the cover platform."""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fakes import FakeConfigEntry, make_device, prepare_entity
from homeassistant.components.cover import CoverDeviceClass, CoverEntityFeature
from homeassistant.exceptions import HomeAssistantError

from custom_components.rts_gateway import cover as cover_module
from custom_components.rts_gateway.const import SomfyCommand
from custom_components.rts_gateway.cover import PositionEstimator, SomfyRTSCover
from custom_components.rts_gateway.rolling_code import RollingCodeStore

STEP_INTERVAL = 0.01


def build_cover(hass, **device_overrides):
    device = make_device(**device_overrides)
    from conftest import make_runtime_data

    entry = FakeConfigEntry(
        data={
            "devices": [device],
            "sensors": [],
            "transmitter_id": "radio_frequency.test_radio",
            "enable_rx": True,
        },
        runtime_data=make_runtime_data(),
    )
    hass.data.setdefault("rts_gateway", {})["rolling_codes"] = RollingCodeStore(hass)
    entity = SomfyRTSCover(hass, entry, device)
    prepare_entity(entity, "cover.test")
    return entity


# =============================================================================
# PositionEstimator
# =============================================================================
def test_estimator_idle():
    est = PositionEstimator(1000, 1000)
    assert est.position == 100
    assert not est.is_moving
    assert est.reached_target()


def test_estimator_restore_clamps():
    est = PositionEstimator(1000, 1000)
    est.restore(150)
    assert est.position == 100
    est.restore(-5)
    assert est.position == 0


def test_estimator_interpolation(monkeypatch):
    # Exact binary clock: (base + 0.5) - base == 0.5 on any platform.
    base = 4096.0
    monkeypatch.setattr(time, "monotonic", lambda: base)
    est = PositionEstimator(open_time_ms=1000, close_time_ms=500)

    est.restore(20)
    est.start("up", 90)
    monkeypatch.setattr(time, "monotonic", lambda: base + 0.5)
    assert est.position == 70  # 20 + 50% of the open time
    assert not est.reached_target()

    monkeypatch.setattr(time, "monotonic", lambda: base + 1.0)
    assert est.position == 100
    assert est.reached_target()

    est.stop()
    assert not est.is_moving
    assert est.position == 100


def test_estimator_close_interpolation(monkeypatch):
    base = 4096.0
    monkeypatch.setattr(time, "monotonic", lambda: base)
    est = PositionEstimator(open_time_ms=1000, close_time_ms=200)

    est.restore(80)
    est.start("down", 0)
    monkeypatch.setattr(time, "monotonic", lambda: base + 0.1)
    assert 29 <= est.position <= 31  # 80 - 100/200 * 100
    assert not est.reached_target()

    monkeypatch.setattr(time, "monotonic", lambda: base + 0.2)
    assert est.position == 0
    assert est.reached_target()


# =============================================================================
# Entity
# =============================================================================
def test_features_and_device_class(hass):
    entity = build_cover(hass, device_type="blind")
    assert entity.device_class == CoverDeviceClass.BLIND
    assert entity.icon == "mdi:blinds"
    assert entity.supported_features == (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )
    assert entity.current_cover_tilt_position is None


def test_tilt_features_per_protocol(hass):
    # 80-bit blind: step frames.
    tilt = build_cover(hass, device_type="blind", protocol="rts_80bit", tilt=True, tilt_step_percent=10)
    assert CoverEntityFeature.SET_TILT_POSITION in tilt.supported_features
    assert tilt.current_cover_tilt_position == 0

    # 56-bit blind: timed tilt motor.
    timed = build_cover(hass, device_type="blind", protocol="rts_56bit", tilt=True)
    assert CoverEntityFeature.SET_TILT_POSITION in timed.supported_features
    assert timed.current_cover_tilt_position == 0

    # 56-bit blind without the tilt option: no tilt.
    no_tilt = build_cover(hass, device_type="blind", protocol="rts_56bit")
    assert CoverEntityFeature.SET_TILT_POSITION not in no_tilt.supported_features
    assert no_tilt.current_cover_tilt_position is None



def test_open_close_stop_send_commands(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()

        await entity.async_open_cover()
        assert fake_rf.sent[-1]["command"] == "UP"
        assert entity.is_opening is True

        await entity.async_stop_cover()
        assert fake_rf.sent[-1]["command"] == "STOP"
        assert entity.is_opening is None

        await entity.async_close_cover()
        assert fake_rf.sent[-1]["command"] == "DOWN"
        assert entity.is_closing is True

        assert no_timer  # tracking was started and stopped

    asyncio.run(scenario())


def test_set_cover_position(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()

        await entity.async_set_cover_position(position=50)
        assert fake_rf.sent[-1]["command"] == "DOWN"
        assert entity.is_closing is True

        # While moving, requesting the same target re-sends the command.
        await entity.async_set_cover_position(position=50)
        assert len(fake_rf.sent) == 2

        # No movement for the current position once stopped.
        await entity.async_stop_cover()
        count = len(fake_rf.sent)
        await entity.async_set_cover_position(position=entity.current_cover_position)
        assert len(fake_rf.sent) == count

    asyncio.run(scenario())


def test_tick_sends_stop_at_intermediate_target(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()

        est = entity._position_estimator
        est._start_position = 0
        est._direction = "up"
        est._target = 50
        est._started_at = time.monotonic() - 0.6  # 60% of the 1000 ms open time

        entity._start_tracking()
        await entity._async_movement_tick(None)

        assert fake_rf.sent[-1]["command"] == "STOP"
        assert est.direction is None
        assert no_timer  # timer cancelled

    asyncio.run(scenario())


def test_restore_of_last_state(hass):
    async def scenario():
        entity = build_cover(hass)
        last = SimpleNamespace(
            state="open",
            attributes={"current_position": 42, "current_tilt_position": 30},
        )
        entity.async_get_last_state = AsyncMock(return_value=last)
        await entity.async_added_to_hass()
        assert entity.current_cover_position == 42
        # 56-bit cover: tilt is not restored.
        assert entity.current_cover_tilt_position is None

        tilt_entity = build_cover(hass, protocol="rts_80bit", tilt=True, tilt_step_percent=10)
        tilt_entity.async_get_last_state = AsyncMock(return_value=last)
        await tilt_entity.async_added_to_hass()
        assert tilt_entity.current_cover_tilt_position == 30

    asyncio.run(scenario())


def test_registry_lifecycle(hass):
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()
        registry = entity._entry.runtime_data.entities
        assert registry["cover.test"] is entity

        await entity.async_will_remove_from_hass()
        assert "cover.test" not in registry

    asyncio.run(scenario())


def test_rx_short_press_moves_lift(hass, monkeypatch):
    """Un appui court (peu de repetitions) = montee/descente."""
    monkeypatch.setattr(cover_module, "TILT_PRESS_END_S", 0.05)
    monkeypatch.setattr(cover_module, "_PRESS_POLL_S", 0.01)

    async def scenario():
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True
        )
        await entity.async_added_to_hass()

        # Une trame unique puis silence : appui court.
        entity.async_update_from_rx(SomfyCommand.UP, 10, "remote", 0, 1)
        assert entity._press is not None  # decision en attente
        await asyncio.sleep(0.2)
        assert entity._press is None
        assert entity.is_opening is True
        assert entity.current_cover_tilt_position == 0  # pas de tilt

    asyncio.run(scenario())


def test_rx_long_press_tilts(hass, monkeypatch):
    """Un appui long (>= 15 repetitions) = inclinaison."""
    monkeypatch.setattr(cover_module, "TILT_PRESS_END_S", 0.05)
    monkeypatch.setattr(cover_module, "_PRESS_POLL_S", 0.01)
    monkeypatch.setattr(cover_module, "_TILT_TIMED_TICK_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True, tilt_time_ms=100
        )
        await entity.async_added_to_hass()

        entity.async_update_from_rx(SomfyCommand.UP, 10, "remote", 0, 1)
        for repeat in range(2, 16):
            entity.async_update_from_rx(
                SomfyCommand.UP, 10, "remote", 0, repeat
            )
        await asyncio.sleep(0.2)
        assert entity.is_opening is None  # la montee n'a pas bouge
        assert entity._tilt_track is None  # tilt termine (cible atteinte)
        assert entity.current_cover_tilt_position == 100

        # MY depuis la telecommande ne fait rien de plus.
        entity.async_update_from_rx(SomfyCommand.MY, 11, "remote", 0, 1)
        assert entity.current_cover_tilt_position == 100

    asyncio.run(scenario())


def test_rx_long_press_down_then_my_freezes(hass, monkeypatch):
    """MY congele l'estimate d'inclinaison en cours de mouvement."""
    monkeypatch.setattr(cover_module, "TILT_PRESS_END_S", 0.05)
    monkeypatch.setattr(cover_module, "_PRESS_POLL_S", 0.01)
    monkeypatch.setattr(cover_module, "_TILT_TIMED_TICK_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True, tilt_time_ms=5000
        )
        await entity.async_added_to_hass()
        entity.async_set_current_tilt_position(100)  # lamelles ouvertes

        for repeat in range(1, 16):
            entity.async_update_from_rx(
                SomfyCommand.DOWN, 10, "remote", 0, repeat
            )
        await asyncio.sleep(0.2)  # resolution : tilt vers 0
        assert entity._tilt_track is not None
        await asyncio.sleep(STEP_INTERVAL * 4)
        partial = entity.current_cover_tilt_position
        assert 0 < partial < 100

        entity.async_update_from_rx(SomfyCommand.MY, 11, "remote", 0, 1)
        assert entity._tilt_track is None  # suivi congele
        await asyncio.sleep(STEP_INTERVAL * 6)
        assert entity.current_cover_tilt_position == partial

    asyncio.run(scenario())


def test_rx_press_repeats_forwarded_once(hass, monkeypatch):
    """Le compteur cumulative du rx_handler alimente la decision."""
    monkeypatch.setattr(cover_module, "TILT_PRESS_END_S", 0.05)
    monkeypatch.setattr(cover_module, "_PRESS_POLL_S", 0.01)

    async def scenario():
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True
        )
        await entity.async_added_to_hass()

        entity.async_update_from_rx(SomfyCommand.UP, 10, "remote", 0, 5)
        assert entity._press["count"] == 5
        entity.async_update_from_rx(SomfyCommand.UP, 10, "remote", 0, 15)
        assert entity._press["count"] == 15
        await asyncio.sleep(0.2)
        assert entity._tilt_track is not None or entity.is_opening is None

    asyncio.run(scenario())


def test_timed_tilt_moves_and_stops(hass, fake_rf, monkeypatch):
    """56 bits : Up puis MY apres la duree calculee."""
    monkeypatch.setattr(cover_module, "_TILT_TIMED_TICK_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True, tilt_time_ms=100
        )
        await entity.async_added_to_hass()
        assert entity._tilt_time_ms == 100

        await entity.async_set_cover_tilt_position(tilt_position=100)
        await asyncio.sleep(0.3)
        commands = [f["command"] for f in fake_rf.sent]
        assert commands[0] == "UP"
        assert commands[-1] == "STOP"
        assert entity.current_cover_tilt_position == 100

    asyncio.run(scenario())


def test_timed_tilt_stop_mid_move(hass, fake_rf, monkeypatch):
    """Un arret en cours de mouvement envoie MY au moteur."""
    monkeypatch.setattr(cover_module, "_TILT_TIMED_TICK_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True, tilt_time_ms=2000
        )
        await entity.async_added_to_hass()

        await entity.async_set_cover_tilt_position(tilt_position=100)
        await asyncio.sleep(STEP_INTERVAL * 4)
        partial = entity.current_cover_tilt_position
        assert 0 < partial < 100

        await entity.async_stop_cover_tilt()
        assert fake_rf.sent[-1]["command"] == "STOP"
        assert entity.current_cover_tilt_position == partial  # estimate conservee
        await asyncio.sleep(STEP_INTERVAL * 4)
        assert entity.current_cover_tilt_position == partial  # la tache est bien morte

    asyncio.run(scenario())


def test_will_remove_cancels_tilt(hass, fake_rf, monkeypatch):
    monkeypatch.setattr(cover_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(hass, protocol="rts_80bit", tilt=True, tilt_step_percent=10)
        await entity.async_set_cover_tilt_position(tilt_position=100)
        await asyncio.sleep(STEP_INTERVAL * 2)
        task = entity._tilt_task
        await entity.async_will_remove_from_hass()
        await asyncio.sleep(0)
        assert task.cancelled()

    asyncio.run(scenario())


# =============================================================================
# RX updates
# =============================================================================
def test_rx_updates_position_tracking(hass, fake_rf, no_timer):
    entity = build_cover(hass)

    entity.async_update_from_rx(SomfyCommand.UP, 10, "remote", 0)
    assert entity.is_opening is True

    entity.async_update_from_rx(SomfyCommand.DOWN, 11, "remote", 0)
    assert entity.is_closing is True

    entity.async_update_from_rx(SomfyCommand.MY, 12, "remote", 0)
    assert entity.is_opening is None and entity.is_closing is None

    attributes = entity.extra_state_attributes
    assert attributes["last_rx_rolling_code"] == 12
    assert attributes["last_rx_device"] == "remote"


def test_rx_step_updates_tilt(hass):
    entity = build_cover(hass, protocol="rts_80bit", tilt=True, tilt_step_percent=10)
    entity.async_update_from_rx(SomfyCommand.STEP_UP, 1, "remote", 5)
    assert entity.current_cover_tilt_position == 10
    entity.async_update_from_rx(SomfyCommand.STEP_DOWN, 2, "remote", 5)
    assert entity.current_cover_tilt_position == 0

    # Steps are ignored without tilt support.
    plain = build_cover(hass)
    plain.async_update_from_rx(SomfyCommand.STEP_UP, 1, "remote", 5)
    assert plain.current_cover_tilt_position is None


def test_rx_sensor_updates_alarm_attributes(hass):
    entity = build_cover(hass)
    entity.async_update_sensor_state(0x03, "esphome")
    attributes = entity.extra_state_attributes
    assert attributes["wind_alarm"] is True
    assert attributes["sun_alarm"] is True
    assert attributes["last_rx_rolling_code"] == 0x03

    entity.async_update_sensor_state(0x02, "esphome")
    assert entity.extra_state_attributes["wind_alarm"] is False
    assert entity.extra_state_attributes["sun_alarm"] is True


# =============================================================================
# Tilt
# =============================================================================
def test_tilt_runs_to_target(hass, fake_rf, monkeypatch):
    monkeypatch.setattr(cover_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(hass, protocol="rts_80bit", tilt=True, tilt_step_percent=10)
        await entity.async_set_cover_tilt_position(tilt_position=80)
        await entity._tilt_task

        assert entity.current_cover_tilt_position == 80
        assert [s["command"] for s in fake_rf.sent] == ["STEP_UP"] * 8

    asyncio.run(scenario())


def test_tilt_stop_keeps_partial_estimate(hass, fake_rf, monkeypatch):
    """Regression: stopping mid-movement must not snap to the target."""
    monkeypatch.setattr(cover_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(hass, protocol="rts_80bit", tilt=True, tilt_step_percent=10)
        await entity.async_set_cover_tilt_position(tilt_position=100)
        await asyncio.sleep(STEP_INTERVAL * 4)
        task = entity._tilt_task
        await entity.async_stop_cover_tilt()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        tilt = entity.current_cover_tilt_position
        assert 0 < tilt < 100
        assert tilt == 10 * len(fake_rf.sent)  # one step per sent frame

    asyncio.run(scenario())


def test_tilt_restart_from_partial(hass, fake_rf, monkeypatch):
    monkeypatch.setattr(cover_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(hass, protocol="rts_80bit", tilt=True, tilt_step_percent=10)
        await entity.async_set_cover_tilt_position(tilt_position=100)
        await asyncio.sleep(STEP_INTERVAL * 3)
        task = entity._tilt_task
        await entity.async_stop_cover_tilt()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        partial = entity.current_cover_tilt_position

        # A new tilt command starts from the partial estimate.
        await entity.async_set_cover_tilt_position(tilt_position=40)
        await entity._tilt_task
        assert entity.current_cover_tilt_position == 40
        # First command after the restart moves up from the partial value.
        assert partial < 40

    asyncio.run(scenario())


def test_update_linked_remotes(hass):
    entity = build_cover(hass, linked_remote_addresses=["00000001"])
    entity.async_update_linked_remotes(["00000002", "00000003"])
    assert entity.linked_remote_addresses_int == [2, 3]
    assert entity._device_config["linked_remote_addresses"] == ["00000002", "00000003"]


# =============================================================================
# Inversion (moteur monte a l'envers)
# =============================================================================
def test_invert_swaps_tx_commands(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass, invert=True)
        await entity.async_added_to_hass()

        await entity.async_open_cover()
        assert fake_rf.sent[-1]["command"] == "DOWN"
        assert entity.is_opening is True  # logique HA inchangee

        await entity.async_stop_cover()
        assert fake_rf.sent[-1]["command"] == "STOP"

        await entity.async_close_cover()
        assert fake_rf.sent[-1]["command"] == "UP"
        assert entity.is_closing is True

        assert entity.extra_state_attributes["invert"] is True

    asyncio.run(scenario())


def test_invert_set_position_sends_swapped_command(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass, invert=True)
        await entity.async_added_to_hass()

        await entity.async_set_cover_position(position=50)
        assert fake_rf.sent[-1]["command"] == "UP"  # descente logique = UP radio

    asyncio.run(scenario())


def test_invert_swaps_rx_commands(hass, fake_rf, no_timer):
    entity = build_cover(hass, invert=True)

    # Radio UP recu -> le moteur descend (logique HA inversee).
    entity.async_update_from_rx(SomfyCommand.UP, 10, "remote", 0)
    assert entity.is_closing is True

    entity.async_update_from_rx(SomfyCommand.STOP, 11, "remote", 0)
    assert entity.is_closing is None

    entity.async_update_from_rx(SomfyCommand.DOWN, 12, "remote", 0)
    assert entity.is_opening is True


def test_invert_swaps_tilt_steps(hass, fake_rf, monkeypatch):
    monkeypatch.setattr(cover_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)

    async def scenario():
        entity = build_cover(hass, protocol="rts_80bit", tilt=True, invert=True,
                            tilt_step_percent=10)
        await entity.async_set_cover_tilt_position(tilt_position=40)
        await entity._tilt_task
        # Montee logique d'inclinaison = STEP_DOWN radio.
        assert {s["command"] for s in fake_rf.sent} == {"STEP_DOWN"}

        # Pas radio STEP_UP recu -> inclinaison logique descendante.
        entity.async_update_from_rx(SomfyCommand.STEP_UP, 3, "remote", 5)
        assert entity.current_cover_tilt_position == 40 - 10

    asyncio.run(scenario())


def test_no_invert_by_default(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()
        await entity.async_open_cover()
        assert fake_rf.sent[-1]["command"] == "UP"
        assert entity.extra_state_attributes["invert"] is False

    asyncio.run(scenario())


# =============================================================================
# Inversion de l'echelle de position (ouvert = 0 %, ferme = 100 %)
# =============================================================================
def test_invert_position_reporting(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass, invert_position=True)
        await entity.async_added_to_hass()
        assert entity.current_cover_position == 0  # ouvert par defaut = 0 %

        await entity.async_close_cover()
        assert entity.is_closing is True
        assert entity.extra_state_attributes["invert_position"] is True

    asyncio.run(scenario())


def test_invert_position_closed_reads_100(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass, invert_position=True)
        await entity.async_added_to_hass()
        est = entity._position_estimator
        est._start_position = 100
        est._direction = "down"
        est._target = 0
        est._started_at = time.monotonic() - 1.5  # au-dela du temps de fermeture
        entity._start_tracking()
        await entity._async_movement_tick(None)
        assert entity.current_cover_position == 100  # ferme = 100 %
        assert entity.is_closed is True  # la semantique HA reste physique

    asyncio.run(scenario())


def test_invert_position_set_position(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass, invert_position=True)
        await entity.async_added_to_hass()

        # L'utilisateur demande 30 % a l'affichage = 30 % "ferme" = 70 logique.
        await entity.async_set_cover_position(position=30)
        assert fake_rf.sent[-1]["command"] == "DOWN"  # de 100 (ouvert) vers 70
        assert entity.is_closing is True

    asyncio.run(scenario())


def test_invert_position_restore(hass):
    async def scenario():
        entity = build_cover(hass, invert_position=True)
        last = SimpleNamespace(
            state="open", attributes={"current_position": 25}
        )
        entity.async_get_last_state = AsyncMock(return_value=last)
        await entity.async_added_to_hass()
        # 25 % affiche (valeur stockee) = 75 % logique.
        assert entity.current_cover_position == 25
        assert entity._position_estimator.position == 75

    asyncio.run(scenario())


def test_position_not_inverted_by_default(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()
        assert entity.current_cover_position == 100  # ouvert par defaut
        assert entity.extra_state_attributes["invert_position"] is False

    asyncio.run(scenario())


# =============================================================================
# Position favorite My
# =============================================================================
def test_my_position_goto(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass, my_position=30)
        await entity.async_added_to_hass()
        assert entity.extra_state_attributes["my_position"] == 30

        await entity.async_goto_my_position()
        # Position favorite 30 (echelle affichee) -> descente logique.
        assert fake_rf.sent[-1]["command"] == "DOWN"
        assert entity.is_closing is True

    asyncio.run(scenario())


def test_my_position_none_by_default(hass):
    entity = build_cover(hass)
    assert entity._my_position is None
    assert entity.extra_state_attributes["my_position"] is None


def test_my_position_respects_invert_position(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass, my_position=30, invert_position=True)
        await entity.async_added_to_hass()

        await entity.async_goto_my_position()
        # 30 % affiche = 70 logique depuis 100 (ouvert) -> descente logique.
        assert fake_rf.sent[-1]["command"] == "DOWN"

    asyncio.run(scenario())


# =============================================================================
# Recalage manuel
# =============================================================================
def test_stop_method_pulse_sends_opposite(hass, fake_rf, no_timer):
    """Un moteur sans MY sarrete par une impulsion opposee."""
    async def scenario():
        entity = build_cover(hass, stop_method="pulse")
        await entity.async_added_to_hass()

        # Descente vers 30 : sarrete par une breve impulsion UP.
        await entity.async_set_cover_position(position=30)
        assert fake_rf.sent[-1]["command"] == "DOWN"
        entity._position_estimator._started_at -= 10  # simule le temps passe
        await entity._async_movement_tick(None)
        assert fake_rf.sent[-1]["command"] == "UP"

    asyncio.run(scenario())


def test_stop_method_my_sends_my(hass, fake_rf, no_timer):
    """Methode par defaut : arret par MY."""
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()
        assert entity._stop_method == "my"

        await entity.async_set_cover_position(position=30)
        entity._position_estimator._started_at -= 10
        await entity._async_movement_tick(None)
        assert fake_rf.sent[-1]["command"] == "STOP"

    asyncio.run(scenario())


def test_rx_native_my_tracks_to_favorite(hass, no_timer):
    """MY court recu en natif : suivi vers my_position, pas de freeze."""
    entity = build_cover(hass, my_position=30, simulate_my=False)
    assert entity.current_cover_position == 100

    entity.async_update_from_rx(SomfyCommand.MY, 10, "remote", 0, 1)
    assert entity.is_closing is True  # suivi vers 30


def test_rx_native_long_my_memorizes_freeze(hass, no_timer):
    """MY long recu (>= 35 repetitions) : memorisation, rien ne bouge."""
    entity = build_cover(hass, my_position=30, simulate_my=False)

    entity.async_update_from_rx(SomfyCommand.MY, 10, "remote", 0, 35)
    assert entity.is_closing is None  # estimate figee a 100
    assert entity.current_cover_position == 100


def test_rx_simulated_my_drives_to_favorite(hass, fake_rf, no_timer):
    """MY court recu en simule : HA conduit le volet vers my_position."""
    async def scenario():
        entity = build_cover(hass, my_position=30)
        await entity.async_added_to_hass()

        entity.async_update_from_rx(SomfyCommand.MY, 10, "remote", 0, 1)
        await asyncio.sleep(0)  # laisse la tache demarree s executer
        assert fake_rf.sent[-1]["command"] == "DOWN"
        assert entity.is_closing is True  # conduit vers 30

    asyncio.run(scenario())


def test_rx_simulated_my_without_position_freezes(hass, no_timer):
    """MY recu en simule sans my_position : rien a faire -> freeze."""
    entity = build_cover(hass)

    entity.async_update_from_rx(SomfyCommand.MY, 10, "remote", 0, 1)
    assert entity.is_closing is None


def test_rx_native_my_inverted_scales(hass, no_timer):
    """Echelle inversee : le suivi natif part dans le bon sens."""
    entity = build_cover(
        hass, my_position=30, simulate_my=False, invert_position=True
    )
    # Volet ouvert = 0 affiche en echelle inversee (100 logique).

    entity.async_update_from_rx(SomfyCommand.MY, 10, "remote", 0, 1)
    # my_position 30 affiche = 70 logique < 100 logique -> descente.
    assert entity.is_closing is True


def test_native_my_sends_frame_and_tracks(hass, fake_rf, no_timer):
    """MY natif : trame MY au moteur, estimation vers my_position."""
    async def scenario():
        entity = build_cover(hass, my_position=30, simulate_my=False)
        await entity.async_added_to_hass()
        assert entity._simulate_my is False

        await entity.async_goto_my_position()
        assert fake_rf.sent[-1]["command"] == "MY"
        assert entity.is_closing is True  # suivi vers 30

    asyncio.run(scenario())


def test_memorize_my_position_long_press(hass, fake_rf, no_timer):
    """Mamorisation native : appui MY long (35 repetitions)."""
    async def scenario():
        entity = build_cover(hass, simulate_my=False)
        await entity.async_added_to_hass()

        await entity.async_memorize_my_position()
        assert fake_rf.sent[-1]["command"] == "MY"
        assert fake_rf.sent[-1]["repeats"] == 35

    asyncio.run(scenario())


def test_recalibrate_goes_to_nearest_end(hass, fake_rf, no_timer):
    """Recalage : butee la plus proche (haute depuis 100)."""
    async def scenario():
        entity = build_cover(hass, open_time_ms=100, close_time_ms=100)
        await entity.async_added_to_hass()

        entity.async_recalibrate()
        assert entity._recal_task is not None
        await entity._recal_task  # ~1.1 s avec une course de 100 ms
        commands = [f["command"] for f in fake_rf.sent]
        assert commands == ["UP"]  # depuis 100, la butee haute est la plus proche
        assert entity.current_cover_position == 100

    asyncio.run(scenario())


def test_recalibrate_returns_to_start_position(hass, fake_rf, no_timer):
    """Recalage : le volet revient a sa position de depart."""
    async def scenario():
        entity = build_cover(hass, open_time_ms=100, close_time_ms=100)
        await entity.async_added_to_hass()
        entity.async_set_current_position(50)

        entity.async_recalibrate()
        await entity._recal_task
        commands = [f["command"] for f in fake_rf.sent]
        assert commands == ["DOWN", "UP"]  # butee basse (50), retour vers 50
        assert entity.is_opening is True  # retour vers 50 en cours
        entity._position_estimator.stop()
        entity._stop_tracking()

    asyncio.run(scenario())


def test_recalibrate_ignores_duplicate(hass, fake_rf, no_timer):
    """Un recalage en cours ignore un second declenchement."""
    async def scenario():
        entity = build_cover(hass, open_time_ms=100, close_time_ms=100)
        await entity.async_added_to_hass()

        entity.async_recalibrate()
        first = entity._recal_task
        entity.async_recalibrate()
        assert entity._recal_task is first
        first.cancel()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_set_current_position(hass, fake_rf, no_timer):
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()

        entity.async_set_current_position(42)
        assert entity.current_cover_position == 42
        assert entity._position_estimator.direction is None

        # Avec inversion d'echelle : 30 affiche = 70 logique.
        inverted = build_cover(hass, invert_position=True)
        inverted.async_set_current_position(30)
        assert inverted.current_cover_position == 30
        assert inverted._position_estimator.position == 70

    asyncio.run(scenario())


def test_set_current_tilt_position(hass):
    entity = build_cover(hass, protocol="rts_80bit", tilt=True)
    entity.async_set_current_tilt_position(55)
    assert entity.current_cover_tilt_position == 55


# =============================================================================
# Commandes brutes
# =============================================================================
def test_send_raw_command_bypasses_invert(hass, fake_rf):
    async def scenario():
        entity = build_cover(hass, invert=True)
        # Commande brute radio : UP reste UP malgre l'inversion logique.
        await entity.async_send_raw_command(SomfyCommand.UP, frame_repeats=1)
        assert fake_rf.sent[-1]["command"] == "UP"

    asyncio.run(scenario())


def test_send_raw_command_with_step_size(hass, fake_rf):
    async def scenario():
        entity = build_cover(hass, protocol="rts_80bit", tilt=True)
        await entity.async_send_raw_command(
            SomfyCommand.STEP_UP, frame_repeats=1, step_size=77
        )
        assert fake_rf.sent[-1]["command"] == "STEP_UP"
        assert fake_rf.sent[-1]["step_size"] == 77

    asyncio.run(scenario())


def test_send_raw_step_requires_80bit(hass, monkeypatch):
    async def scenario():
        from custom_components.rts_gateway import rf

        async def fake_native(hass, transmitter_id, command):
            pass

        monkeypatch.setattr(rf, "rf_async_send_command", fake_native)
        entity = build_cover(hass)  # 56 bits : validation de la vraie chaine
        with pytest.raises(ValueError):
            await entity.async_send_raw_command(SomfyCommand.STEP_UP, frame_repeats=1)

    asyncio.run(scenario())


# =============================================================================
# Disponibilite (checklist entity-unavailable / log-when-unavailable)
# =============================================================================
# =============================================================================
# Branches de couverture complementaires
# =============================================================================
def test_setup_entry_filters_cover_types(hass):
    """Le setup ne cree que les types couverture."""
    from conftest import make_runtime_data
    from fakes import FakeConfigEntry

    from custom_components.rts_gateway import cover as cover_module

    hass.data.setdefault("rts_gateway", {})["rolling_codes"] = RollingCodeStore(hass)
    entry = FakeConfigEntry(
        data={
            "devices": [
                make_device(device_type="roller_shutter"),
                make_device(device_type="socket"),
            ],
            "sensors": [],
            "transmitter_id": "radio_frequency.test_radio",
        },
        runtime_data=make_runtime_data(),
    )
    added = []

    async def scenario():
        await cover_module.async_setup_entry(
            hass, entry, lambda ents: added.extend(ents)
        )

    asyncio.run(scenario())
    assert len(added) == 1
    assert isinstance(added[0], cover_module.SomfyRTSCover)


def test_entity_name_is_none(hass):
    """Le nom vient de l'appareil (has_entity_name)."""
    entity = build_cover(hass)
    assert entity.name is None


def test_will_remove_cancels_recalibration(hass, fake_rf, no_timer):
    """will_remove annule un recalage en cours."""
    async def scenario():
        entity = build_cover(hass, open_time_ms=100, close_time_ms=100)
        await entity.async_added_to_hass()

        entity.async_recalibrate()
        task = entity._recal_task
        assert task is not None
        await entity.async_will_remove_from_hass()
        await asyncio.sleep(0)
        assert task.cancelled()


    asyncio.run(scenario())


def test_stop_cover_while_idle_sends_my(hass, fake_rf, no_timer):
    """Stop sans mouvement : simple trame STOP."""
    async def scenario():
        entity = build_cover(hass)
        await entity.async_added_to_hass()

        await entity.async_stop_cover()
        assert fake_rf.sent[-1]["command"] == "STOP"


    asyncio.run(scenario())


def test_stop_at_target_pulse_down_sends_down(hass, fake_rf, no_timer):
    """Pulse : un mouvement vers le haut sarrete par une impulsion DOWN."""
    async def scenario():
        entity = build_cover(hass, stop_method="pulse")
        await entity.async_added_to_hass()

        entity._position_estimator.start("up", 100)
        await entity._async_stop_at_target()
        assert fake_rf.sent[-1]["command"] == "DOWN"


    asyncio.run(scenario())


def test_movement_tick_stop_failure_logs(hass, fake_rf, no_timer, monkeypatch, caplog):
    """Echec de l'arret a la cible : warning, l'estimation sarrete quand meme."""
    async def boom(*args, **kwargs):
        raise HomeAssistantError("radio down")

    async def scenario():
        entity = build_cover(hass, my_position=30)
        monkeypatch.setattr(
            entity, "_async_stop_at_target", boom
        )
        await entity.async_added_to_hass()

        await entity.async_set_cover_position(position=30)
        entity._position_estimator._started_at -= 60
        await entity._async_movement_tick(None)
        assert "Failed to stop" in caplog.text
        assert not entity._position_estimator.is_moving


    asyncio.run(scenario())


def test_movement_tick_stops_tracking_when_idle(hass, no_timer):
    """Tick sans mouvement : arrete le suivi."""
    async def scenario():
        entity = build_cover(hass)
        entity._position_estimator.stop()
        await entity._async_movement_tick(None)
        assert entity._cancel_timer is None

    asyncio.run(scenario())


def test_timed_tilt_zero_delta_is_noop(hass, fake_rf, monkeypatch):
    """Tilt temporise sans ecart : aucune trame."""
    async def scenario():
        monkeypatch.setattr(cover_module, "_TILT_TIMED_TICK_S", STEP_INTERVAL)
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True
        )
        entity.async_set_current_tilt_position(100)

        await entity._async_tilt_to_target_timed(100)
        assert fake_rf.sent == []


    asyncio.run(scenario())


def test_timed_tilt_stop_failure_logs(hass, fake_rf, monkeypatch, caplog):
    """Echec du MY final du tilt temporise : warning."""
    calls = []

    async def boom(command, frame_repeats=3):
        # Le mouvement passe, l'arret final echoue.
        calls.append(command)
        if command == SomfyCommand.STOP:
            raise HomeAssistantError("radio down")

    async def scenario():
        monkeypatch.setattr(cover_module, "_TILT_TIMED_TICK_S", STEP_INTERVAL)
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True,
            tilt_time_ms=100,
        )
        await entity.async_added_to_hass()
        monkeypatch.setattr(entity, "_async_send_command", boom)

        await entity._async_tilt_to_target_timed(50)
        assert "Failed to stop tilt" in caplog.text


    asyncio.run(scenario())


def test_step_tilt_zero_delta_is_noop(hass, fake_rf, no_timer):
    """Tilt 80 bits sans ecart : aucune trame."""
    entity = build_cover(
        hass, device_type="blind", protocol="rts_80bit", tilt=True
    )
    entity.async_set_current_tilt_position(50)
    asyncio.run(entity._async_tilt_to_target_step(50))
    assert fake_rf.sent == []


def test_open_close_tilt_shortcuts(hass, fake_rf, monkeypatch):
    """open_tilt/close_tilt deleguent au mecanisme du protocole."""
    async def scenario():
        monkeypatch.setattr(cover_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)
        entity = build_cover(
            hass, device_type="blind", protocol="rts_80bit", tilt=True,
            tilt_step_percent=10,
        )
        await entity.async_added_to_hass()
        entity.async_set_current_tilt_position(50)

        await entity.async_open_cover_tilt()
        await entity._tilt_task
        assert entity.current_cover_tilt_position == 100
        await asyncio.sleep(0.05)

        await entity.async_close_cover_tilt()
        await entity._tilt_task
        assert entity.current_cover_tilt_position == 0


    asyncio.run(scenario())


def test_native_my_tracking_edge_cases(hass, no_timer):
    """Suivi natif : sans my_position ou deja en place -> no-op."""
    entity = build_cover(hass, simulate_my=False)
    entity._native_my_tracking()  # my_position None
    assert not entity._position_estimator.is_moving

    entity2 = build_cover(hass, simulate_my=False, my_position=100)
    entity2._native_my_tracking()  # deja a la cible
    assert not entity2._position_estimator.is_moving


def test_rx_short_down_press_moves_down(hass, fake_rf, monkeypatch, no_timer):
    """Appui court DOWN resolu : descente."""
    async def scenario():
        monkeypatch.setattr(cover_module, "TILT_PRESS_END_S", 0.05)
        monkeypatch.setattr(cover_module, "_PRESS_POLL_S", 0.01)
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True,
            my_position=30,
        )
        await entity.async_added_to_hass()
        entity.async_set_current_position(90)

        entity.async_update_from_rx(SomfyCommand.DOWN, 10, "remote", 0, 1)
        await asyncio.sleep(0.2)
        assert entity.is_closing is True
        assert entity._press is None  # resolution terminee


    asyncio.run(scenario())


def test_rx_press_cancelled_by_stop(hass, fake_rf, monkeypatch, no_timer):
    """Un MY pendant une resolution d'appui annule la decision."""
    async def scenario():
        monkeypatch.setattr(cover_module, "TILT_PRESS_END_S", 0.05)
        monkeypatch.setattr(cover_module, "_PRESS_POLL_S", 0.01)
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True,
        )
        await entity.async_added_to_hass()

        entity.async_update_from_rx(SomfyCommand.UP, 10, "remote", 0, 1)
        task = entity._press_task
        assert task is not None
        entity.async_update_from_rx(SomfyCommand.MY, 11, "remote", 0, 1)
        await asyncio.sleep(0.2)
        assert entity.is_closing is None  # decision annulee, rien bouge


    asyncio.run(scenario())


def test_available_follows_transmitter(hass, caplog):
    """L entite suit la disponibilite de l emetteur RF."""
    import logging
    from types import SimpleNamespace

    caplog.set_level(logging.INFO)

    entity = build_cover(hass)
    hass.states.set(
        "radio_frequency.test_radio", SimpleNamespace(state="on")
    )
    assert entity.available is True

    # Emetteur indisponible -> entite indisponible, log unique.
    hass.states.set(
        "radio_frequency.test_radio", SimpleNamespace(state="unavailable")
    )
    assert entity.available is False
    assert "RF transmitter is unavailable" in caplog.text
    assert caplog.text.count("RF transmitter is unavailable") == 1

    # Retour -> disponible, log unique.
    hass.states.set(
        "radio_frequency.test_radio", SimpleNamespace(state="on")
    )
    assert entity.available is True
    assert "RF transmitter is available again" in caplog.text


def test_unavailable_without_transmitter_state(hass):
    """Sans etat d emetteur du tout : indisponible."""
    entity = build_cover(hass)
    assert entity.available is False


def test_stop_cover_tilt_failure_logs(hass, fake_rf, no_timer, monkeypatch, caplog):
    """Echec du STOP pendant un tilt pilote par telecommande : warning."""
    async def boom(command, frame_repeats=3):
        if command == SomfyCommand.STOP:
            raise HomeAssistantError("radio down")

    async def scenario():
        monkeypatch.setattr(cover_module, "_TILT_TIMED_TICK_S", STEP_INTERVAL)
        monkeypatch.setattr(cover_module, "TILT_PRESS_END_S", 0.05)
        monkeypatch.setattr(cover_module, "_PRESS_POLL_S", 0.01)
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True,
            tilt_time_ms=5000,
        )
        await entity.async_added_to_hass()
        entity.async_set_current_tilt_position(100)
        monkeypatch.setattr(entity, "_async_send_command", boom)

        # Tilt declenche par telecommande (suivi RX) vers 0.
        for repeat in range(1, 16):
            entity.async_update_from_rx(
                SomfyCommand.DOWN, 10, "remote", 0, repeat
            )
        await asyncio.sleep(0.2)
        assert entity._tilt_track_task is not None

        await entity.async_stop_cover_tilt()
        assert "Failed to stop tilt" in caplog.text
        assert entity._tilt_track_task is None

    asyncio.run(scenario())


def test_tilt_tracking_superseded_quiet(hass, fake_rf, monkeypatch):
    """Un deuxieme suivi supplante le premier sans erreur."""
    async def scenario():
        monkeypatch.setattr(cover_module, "_TILT_TIMED_TICK_S", STEP_INTERVAL)
        entity = build_cover(
            hass, device_type="blind", protocol="rts_56bit", tilt=True,
            tilt_time_ms=5000,
        )
        await entity.async_added_to_hass()
        entity.async_set_current_tilt_position(0)

        entity._start_tilt_tracking(100)
        first = entity._tilt_track_task
        entity._start_tilt_tracking(100)  # remplace le precedent
        assert entity._tilt_track_task is not first
        first_task = first
        await asyncio.sleep(STEP_INTERVAL * 4)
        assert first_task.done()  # s'est termine proprement (return)

    asyncio.run(scenario())


# =============================================================================
# Groupes : les commandes du groupe deplacent aussi les membres
# =============================================================================
def build_group_with_members(hass, **group_overrides):
    """Un groupe et ses membres, enregistres dans une meme entree."""
    from conftest import make_runtime_data

    entry = FakeConfigEntry(
        data={
            "devices": [],
            "sensors": [],
            "transmitter_id": "radio_frequency.test_radio",
            "enable_rx": True,
        },
        runtime_data=make_runtime_data(),
    )
    hass.data.setdefault("rts_gateway", {})["rolling_codes"] = RollingCodeStore(hass)
    group = SomfyRTSCover(
        hass,
        entry,
        make_device(name="Groupe", address="1500000", is_group=True,
                    **group_overrides),
    )
    prepare_entity(group, "cover.groupe")
    entry.runtime_data.entities["cover.groupe"] = group
    members = []
    for index, overrides in enumerate((
        {},
        {"open_time_ms": 500, "close_time_ms": 500},
    )):
        member = SomfyRTSCover(
            hass,
            entry,
            make_device(
                name=f"Volet {index}",
                address=f"1000000{index}",
                group_addresses=["1500000"],
                **overrides,
            ),
        )
        prepare_entity(member, f"cover.volet_{index}")
        entry.runtime_data.entities[f"cover.volet_{index}"] = member
        members.append(member)
    hass.config_entries.entries.append(entry)
    return group, members


def test_group_commands_move_members(hass, fake_rf, no_timer):
    """Ouvrir fermer le groupe : les membres suivent, sans radio propre."""
    async def scenario():
        group, members = build_group_with_members(hass)

        await group.async_open_cover()
        assert group.is_opening
        for member in members:
            assert member.is_opening
        # Une seule trame : l'adresse du groupe.
        assert [f["address"] for f in fake_rf.sent] == [1500000]

        await group.async_stop_cover()
        for member in members:
            assert member.is_opening is None
            assert member._shadow_tracking is False

        await group.async_close_cover()
        for member in members:
            assert member.is_closing
        assert [f["address"] for f in fake_rf.sent] == [
            1500000, 1500000, 1500000,
        ]

    asyncio.run(scenario())


def test_group_position_commands_each_member(hass, fake_rf, no_timer, monkeypatch):
    """Une cible intermediaire : chaque membre est commande separement.

    Une seule trame sur l'adresse du groupe ne peut pas poser tous les
    moteurs a la meme position intermediaire (ils entendraient le meme
    arret) : le groupe commande chaque membre individuellement, comme
    le CoverGroup natif de HA et ESPSomfyRTS."""
    async def scenario():
        group, members = build_group_with_members(hass)
        # Vitesses egales pour un arret deterministe au tick de 1 s.
        members[1]._position_estimator.set_travel_times(1000, 1000)

        base = 4096.0
        monkeypatch.setattr(time, "monotonic", lambda: base)
        await group.async_set_cover_position(position=50)

        # Aucune trame sur l'adresse du groupe : chaque membre a emis
        # sa propre commande de descente sur sa propre adresse.
        assert [f["address"] for f in fake_rf.sent] == [10000000, 10000001]
        # Presses de direction alllegees : une seule repetition,
        # comme une telecommande physique.
        assert [f["repeats"] for f in fake_rf.sent] == [1, 1]
        for member in members:
            assert member.is_closing
            assert member._shadow_tracking is False

        # Chaque membre s'arrete a 50 par son propre chronometrage,
        # avec son propre STOP radio.
        monkeypatch.setattr(time, "monotonic", lambda: base + 0.5)
        for member in members:
            await member._async_movement_tick(None)
            assert member.current_cover_position == 50
            assert member.is_closing is None
        assert [f["command"] for f in fake_rf.sent[-2:]] == ["STOP", "STOP"]
        # Le groupe affiche la moyenne des membres.
        assert group.current_cover_position == 50

    asyncio.run(scenario())


def test_group_close_does_not_freeze_members(hass, fake_rf, no_timer, monkeypatch):
    """Regression : une fermeture du groupe ne fige plus les membres.

    L'estimateur interne du groupe peut etre perime (rest'e a 0 depuis
    une ancienne fermeture) : le tick du groupe atteignait sa cible des
    le premier tick et gelait tous les membres a ~1 s de leur propre
    course, alors que les moteurs faisaient la descente complete
    (incident 2026-10-25 : volets affiches a 93-94 % toute la nuit).
    """
    async def scenario():
        group, members = build_group_with_members(hass)
        # Membres ouverts (~97 %) apres restauration, groupe perime a 0.
        for member in members:
            member._position_estimator.restore(97)
        group._position_estimator.restore(0)

        base = 4096.0
        monkeypatch.setattr(time, "monotonic", lambda: base)
        await group.async_close_cover()
        # Le groupe repart de la moyenne des membres, pas de son
        # estimation interne perimee.
        assert group._position_estimator.position == 97
        for member in members:
            assert member.is_closing
            assert member._shadow_tracking is True

        # Le groupe termine sa descente : les membres ne sont PAS
        # gels, ils continuent leur propre course (pas de STOP radio
        # pour une butee).
        monkeypatch.setattr(time, "monotonic", lambda: base + 1.5)
        await group._async_movement_tick(None)
        assert group._position_estimator.is_moving is False
        for member in members:
            assert member._shadow_tracking is True
        assert [f["command"] for f in fake_rf.sent] == ["DOWN"]

        # Les membres terminent chacun a leur rythme jusqu'a la butee.
        monkeypatch.setattr(time, "monotonic", lambda: base + 2.0)
        for member in members:
            await member._async_movement_tick(None)
            assert member.current_cover_position == 0
            assert member.is_closed
        assert group.current_cover_position == 0

    asyncio.run(scenario())


def test_group_travel_times_derive_from_members(hass, fake_rf, no_timer):
    """Un groupe n'a pas de temps de course propres : ils viennent des membres."""
    async def scenario():
        group, members = build_group_with_members(
            hass, open_time_ms=1000, close_time_ms=1000
        )
        # Les membres sont plus lents que la config du groupe.
        members[0]._position_estimator.set_travel_times(4000, 4000)
        members[1]._position_estimator.set_travel_times(2000, 3000)
        await group.async_close_cover()
        # Le groupe se cale sur le membre le plus lent : sa course
        # d'estimation et son STOP radio sur cible intermediaire
        # attendent le temps reel des moteurs.
        assert group._position_estimator.open_time_ms == 4000
        assert group._position_estimator.close_time_ms == 4000

    asyncio.run(scenario())


def test_group_native_my_tracks_own_positions(hass, fake_rf, no_timer):
    """MY natif du groupe : chaque membre vise SA position favorite."""
    async def scenario():
        group, members = build_group_with_members(
            hass, simulate_my=False, my_position=30,
        )
        members[0]._my_position = 40
        members[1]._my_position = 70

        await group.async_goto_my_position()
        assert group.is_closing
        assert members[0]._position_estimator.target == 40
        assert members[1]._position_estimator.target == 70
        for member in members:
            assert member.is_closing

    asyncio.run(scenario())


def test_group_recalibrate_restores_members(hass, fake_rf, no_timer):
    """Le recalage du groupe va a la butee la plus proche et ancre les membres.

    Les sommeils ne sont pas neutralises : l'ancrage est naturel
    (extrapolation jusqu'a la butee), il faut laisser le temps s'ecouler.
    """

    async def scenario():
        group, members = build_group_with_members(hass)

        group.async_recalibrate()
        await group._recal_task  # ~2.3 s (course du groupe de 1000 ms)

        assert group.current_cover_position == 100
        for member in members:
            assert member.current_cover_position == 100
            assert member._shadow_tracking is False

    asyncio.run(scenario())


def test_group_tilt_steps_mirror_members(hass, fake_rf, no_timer, monkeypatch):
    """L'inclinaison du groupe se reflete sur les membres (80 bits)."""
    async def scenario():
        monkeypatch.setattr(cover_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)
        group, members = build_group_with_members(
            hass, device_type="blind", protocol="rts_80bit",
            tilt=True, tilt_step_percent=10,
        )
        for member in members:
            member._tilt_supported = True
            member._tilt_timed = False
            member._tilt_step_percent = 25

        await group.async_set_cover_tilt_position(tilt_position=30)
        await asyncio.sleep(0.2)

        assert group.current_cover_tilt_position == 30
        for member in members:
            assert member.current_cover_tilt_position == 30
        # Les trames partent uniquement de l'adresse du groupe.
        assert {f["address"] for f in fake_rf.sent} == {1500000}

    asyncio.run(scenario())


def test_group_realign_propagates(hass, fake_rf, no_timer):
    """Recaler la position du groupe recale aussi les membres."""
    group, members = build_group_with_members(hass)

    group.async_set_current_position(42)
    for member in members:
        assert member.current_cover_position == 42
        assert member._shadow_tracking is False


def test_group_rx_propagates(hass, fake_rf, no_timer):
    """Une trame recue sur l'adresse du groupe deplace les membres."""
    group, members = build_group_with_members(hass)

    group.async_update_from_rx(SomfyCommand.UP, 5, None)
    for member in members:
        assert member.is_opening
        assert member._shadow_tracking is True

    group.async_update_from_rx(SomfyCommand.STOP, 6, None)
    for member in members:
        assert member.is_opening is None


def test_device_model_localized(hass, fake_rf):
    """Le modele du device suit la langue de HA."""
    from types import SimpleNamespace

    entity = build_cover(hass)
    assert entity._attr_device_info["model"] == "Roller shutter"

    hass.config = SimpleNamespace(language="fr")
    entity_fr = build_cover(hass)
    assert entity_fr._attr_device_info["model"] == "Volet roulant"

def test_group_position_averages_members(hass, fake_rf, no_timer, monkeypatch):
    """Un membre bouge seul : la position du groupe suit la moyenne."""
    async def scenario():
        base = 4096.0
        monkeypatch.setattr(time, "monotonic", lambda: base)
        group, members = build_group_with_members(hass)

        # Tous les volets sont fermes, le groupe aussi.
        group.async_set_current_position(0)
        assert group.current_cover_position == 0
        assert group.is_closed

        # Un seul volet s'ouvre totalement : le groupe passe a 50 %.
        group.async_write_ha_state.reset_mock()
        await members[0].async_set_cover_position(position=100)
        monkeypatch.setattr(time, "monotonic", lambda: base + 2.0)
        await members[0]._async_movement_tick(None)
        assert members[0].current_cover_position == 100
        assert group.current_cover_position == 50
        assert group.is_closed is False
        # L'etat du groupe a bien ete rafraichi.
        assert group.async_write_ha_state.called

        # Fermer le groupe ne doit pas etre avale par l'egalite des
        # cibles (l'estimateur du groupe est deja a 0).
        frames = len(fake_rf.sent)
        await group.async_set_cover_position(position=0)
        assert len(fake_rf.sent) == frames + 1
        monkeypatch.setattr(time, "monotonic", lambda: base + 4.0)
        await group._async_movement_tick(None)
        assert group.current_cover_position == 0
        assert group.is_closed

    asyncio.run(scenario())
