"""Tests for the RX handler."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from conftest import make_runtime_data
from fakes import FakeConfigEntry
from homeassistant.core import Event

from custom_components.somfy_rts.const import (
    DOMAIN,
    RX_EVENT,
    TX_LOOPBACK_TTL,
    SomfyCommand,
)
from custom_components.somfy_rts.rx_handler import SomfyRTSRxHandler


def make_entity(address_int=1000, linked=()):
    return SimpleNamespace(
        _address_int=address_int,
        linked_remote_addresses_int=list(linked),
        async_update_from_rx=MagicMock(),
        async_update_sensor_state=MagicMock(),
    )


def make_sensor(address_int=3000):
    return SimpleNamespace(
        _address_int=address_int,
        async_update_from_rx=MagicMock(),
    )


def make_handler(hass, entities=(), sensors=()):
    entry = FakeConfigEntry(
        data={"devices": [], "sensors": []},
        runtime_data=make_runtime_data(
            entities={f"e{i}": e for i, e in enumerate(entities)},
            sensors={(s._address_int, f"k{i}"): s for i, s in enumerate(sensors)},
        ),
    )
    hass.config_entries.entries.append(entry)
    hass.data[DOMAIN] = {"rolling_codes": object()}
    return SomfyRTSRxHandler(hass)


def fire(handler, *, address, command, rolling_code, device="gw", step=0):
    handler._handle_event(
        Event(RX_EVENT, {"address": str(address), "command": str(command),
                         "rolling_code": str(rolling_code), "device": device,
                         "step": str(step)})
    )


def test_setup_and_cleanup_listen_once(hass):
    handler = SomfyRTSRxHandler(hass)
    handler.async_setup()
    assert RX_EVENT in hass.bus.listeners
    handler.async_cleanup()
    assert RX_EVENT not in hass.bus.listeners


def test_dispatch_matches_own_address(hass):
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    fire(handler, address=1000, command=2, rolling_code=5)
    entity.async_update_from_rx.assert_called_once_with(
        SomfyCommand.UP, 5, "gw", 0, 1
    )


def test_dispatch_matches_linked_remote(hass):
    entity = make_entity(address_int=1000, linked=[2000])
    other = make_entity(address_int=3000)
    handler = make_handler(hass, entities=[entity, other])
    fire(handler, address=2000, command=4, rolling_code=6)
    entity.async_update_from_rx.assert_called_once_with(
        SomfyCommand.DOWN, 6, "gw", 0, 1
    )
    other.async_update_from_rx.assert_not_called()


def test_unlinked_address_is_not_forwarded(hass):
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    fire(handler, address=9999, command=2, rolling_code=5)
    entity.async_update_from_rx.assert_not_called()


def test_loopback_of_own_transmissions_is_ignored(hass):
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    recent_tx = hass.data[DOMAIN].setdefault("recent_tx", {})
    recent_tx[(1000, 5)] = time.monotonic()

    fire(handler, address=1000, command=2, rolling_code=5)
    entity.async_update_from_rx.assert_not_called()

    # The same (address, code) received after the loopback window passes.
    recent_tx[(1000, 5)] = time.monotonic() - TX_LOOPBACK_TTL - 1
    fire(handler, address=1000, command=2, rolling_code=5)
    entity.async_update_from_rx.assert_called_once()


def test_duplicates_are_forwarded_with_their_count(hass):
    """Les repetitions servent a la detection d'appui long : l'evenement
    n'est emis qu'une fois mais les entites voient chaque trame."""
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    fire(handler, address=1000, command=2, rolling_code=5)
    fire(handler, address=1000, command=2, rolling_code=5)
    fire(handler, address=1000, command=2, rolling_code=5)
    assert entity.async_update_from_rx.call_count == 3
    assert entity.async_update_from_rx.call_args_list[-1] == ((
        SomfyCommand.UP, 5, "gw", 0, 3),)
    # A different rolling code (new press) restarts the count.
    fire(handler, address=1000, command=2, rolling_code=6)
    assert entity.async_update_from_rx.call_args_list[-1] == ((
        SomfyCommand.UP, 6, "gw", 0, 1),)


def test_sensor_frames_update_covers_and_binary_sensors(hass):
    cover = make_entity(address_int=1000, linked=[3000])
    sensor = make_sensor(address_int=3000)
    handler = make_handler(hass, entities=[cover], sensors=[sensor])
    # Sensor command nibble is 0xE = 14; rolling_code field holds the payload.
    fire(handler, address=3000, command=14, rolling_code=0x03)
    cover.async_update_sensor_state.assert_called_once_with(0x03, "gw")
    sensor.async_update_from_rx.assert_called_once_with(0x03, "gw")
    cover.async_update_from_rx.assert_not_called()


def test_unmapped_command_is_forwarded_as_none(hass):
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    # 0xF is not mapped (RTW/RTV frames): the entity still learns about
    # the press, with command None.
    fire(handler, address=1000, command=15, rolling_code=5)
    entity.async_update_from_rx.assert_called_once_with(None, 5, "gw", 0, 1)


def test_step_is_forwarded(hass):
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    # 0x8B (139) is the 80-bit Step Up command byte.
    fire(handler, address=1000, command=139, rolling_code=5, step=77)
    entity.async_update_from_rx.assert_called_once_with(
        SomfyCommand.STEP_UP, 5, "gw", 77, 1
    )


def test_malformed_events_are_ignored(hass):
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])

    handler._handle_event(Event(RX_EVENT, {"command": "2", "rolling_code": "5"}))
    handler._handle_event(
        Event(RX_EVENT, {"address": "abc", "command": "2", "rolling_code": "5"})
    )
    handler._handle_event(
        Event(RX_EVENT, {"address": "1000", "command": "x", "rolling_code": "5"})
    )
    entity.async_update_from_rx.assert_not_called()

    # A non-numeric step is not fatal: the frame is dispatched with step 0.
    handler._handle_event(
        Event(RX_EVENT, {"address": "1000", "command": "2", "rolling_code": "5", "step": "bad"})
    )
    entity.async_update_from_rx.assert_called_once_with(SomfyCommand.UP, 5, None, 0, 1)


def test_recent_tx_is_pruned(hass):
    """Regression: recent_tx must not grow without bound."""
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    recent_tx = hass.data[DOMAIN].setdefault("recent_tx", {})
    now = time.monotonic()
    for i in range(300):
        recent_tx[(i, i)] = now - TX_LOOPBACK_TTL - 1

    fire(handler, address=1000, command=2, rolling_code=5)
    assert len(recent_tx) <= 2  # stale entries dropped
    entity.async_update_from_rx.assert_called_once()


# =============================================================================
# Evenements somfy_rts_frame
# =============================================================================
def test_frame_event_fired_for_remote(hass):
    from custom_components.somfy_rts.const import EVENT_FRAME

    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    fire(handler, address=1000, command=2, rolling_code=5)

    events = [e for e in hass.bus.events if e["type"] == EVENT_FRAME]
    assert len(events) == 1
    data = events[0]["data"]
    assert data["address"] == 1000
    assert data["command"] == "UP"
    assert data["command_code"] == 2
    assert data["rolling_code"] == 5
    assert data["source"] == "remote"
    assert data["bits"] == 56


def test_frame_event_fired_once_per_press(hass):
    from custom_components.somfy_rts.const import EVENT_FRAME

    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    fire(handler, address=1000, command=2, rolling_code=5)
    fire(handler, address=1000, command=2, rolling_code=5)  # repetition air

    events = [e for e in hass.bus.events if e["type"] == EVENT_FRAME]
    assert len(events) == 1


def test_frame_event_gateway_loopback(hass):
    from custom_components.somfy_rts.const import EVENT_FRAME

    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    recent_tx = hass.data[DOMAIN].setdefault("recent_tx", {})
    recent_tx[(1000, 5)] = time.monotonic()

    fire(handler, address=1000, command=2, rolling_code=5)
    events = [e for e in hass.bus.events if e["type"] == EVENT_FRAME]
    assert len(events) == 1
    assert events[0]["data"]["source"] == "gateway"
    # Pas de mise a jour d'entite pour le loopback.
    entity.async_update_from_rx.assert_not_called()


def test_frame_event_unknown_address(hass):
    from custom_components.somfy_rts.const import EVENT_FRAME

    handler = make_handler(hass, entities=[])
    fire(handler, address=999999, command=8, rolling_code=1)
    events = [e for e in hass.bus.events if e["type"] == EVENT_FRAME]
    assert len(events) == 1
    assert events[0]["data"]["command"] == "PROG"


def test_frame_event_carries_bits_and_step(hass):
    from custom_components.somfy_rts.const import EVENT_FRAME

    handler = make_handler(hass, entities=[])
    handler._handle_event(
        Event(RX_EVENT, {
            "address": "1000", "command": "139", "rolling_code": "7",
            "device": "gw", "step": "42", "bits": "80",
        })
    )
    events = [e for e in hass.bus.events if e["type"] == EVENT_FRAME]
    assert events[0]["data"]["bits"] == 80
    assert events[0]["data"]["step"] == 42
    assert events[0]["data"]["command"] == "STEP_UP"


# =============================================================================
# Registre des adresses recentes (detection UI)
# =============================================================================
def test_recent_addresses_tracks_remotes(hass):
    handler = make_handler(hass, entities=[])
    fire(handler, address=7611744, command=2, rolling_code=1)
    fire(handler, address=3502923, command=8, rolling_code=2)

    recent = handler.recent_addresses()
    addresses = [item["address"] for item in recent]
    assert sorted(addresses) == [3502923, 7611744]
    # Chaque entree porte la derniere commande et un age court.
    by_addr = {item["address"]: item for item in recent}
    assert by_addr[7611744]["command"] == "UP"
    assert by_addr[3502923]["command"] == "PROG"
    assert all(item["age"] < 5 for item in recent)


def test_recent_addresses_excludes_gateway_loopback(hass):
    handler = make_handler(hass, entities=[])
    recent_tx = hass.data[DOMAIN].setdefault("recent_tx", {})
    recent_tx[(1234567, 1)] = time.monotonic()
    fire(handler, address=1234567, command=2, rolling_code=1)

    assert handler.recent_addresses() == []


def test_recent_addresses_prunes_old_entries(hass):
    handler = make_handler(hass, entities=[])
    fire(handler, address=7611744, command=2, rolling_code=1)
    # Faire vieillir l'entree au-dela de la fenetre de detection.
    now = time.monotonic()
    handler._seen[7611744]["time"] = now - 300.0
    assert handler.recent_addresses() == []
    assert 7611744 not in handler._seen  # epuree


def test_dispatch_skips_entry_without_runtime(hass):
    """Une entree dechargeee (runtime_data absent) est ignoree."""
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])
    # Une seconde entree sans runtime_data.
    ghost = FakeConfigEntry(data={"devices": [], "sensors": []})
    hass.config_entries.entries.append(ghost)

    fire(handler, address=1000, command=2, rolling_code=5)
    entity.async_update_from_rx.assert_called_once()


def test_duplicate_maps_are_pruned(hass):
    """Au-dela de 256 touches, les cartes de deduplication sont elaguees."""
    entity = make_entity(address_int=1000)
    handler = make_handler(hass, entities=[entity])

    # Injecte 260 touches perimes (hors TTL) directement.
    stale = time.monotonic() - 100
    for code in range(260):
        handler._last_frames[(1000 + code, 5)] = stale
    handler._press_counts[(1000, 5)] = 42

    # Une nouvelle trame declenche l'elagage.
    fire(handler, address=1000, command=2, rolling_code=7)
    assert len(handler._last_frames) <= 2
    assert (1000, 5) not in handler._press_counts
    entity.async_update_from_rx.assert_called()
