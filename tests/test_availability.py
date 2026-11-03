"""Tests for the transmitter availability tracking (availability.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from conftest import make_runtime_data
from fakes import FakeConfigEntry, make_device, prepare_entity

from custom_components.rts_gateway import availability as avail
from custom_components.rts_gateway import button as button_module
from custom_components.rts_gateway.cover import SomfyRTSCover
from custom_components.rts_gateway.rolling_code import RollingCodeStore

TRANSMITTER = "radio_frequency.test_radio"


def make_entry(devices):
    return FakeConfigEntry(
        data={
            "devices": devices,
            "sensors": [],
            "transmitter_id": TRANSMITTER,
            "enable_rx": True,
        },
        runtime_data=make_runtime_data(),
    )


def build_cover(hass, **overrides):
    hass.data.setdefault("rts_gateway", {})["rolling_codes"] = RollingCodeStore(hass)
    device = make_device(**overrides)
    entry = make_entry([device])
    entity = SomfyRTSCover(hass, entry, device)
    prepare_entity(entity, "cover.test")
    return entry, entity


def capture_tracker(monkeypatch):
    """Capture the (entity_ids, action) registered by the mixin."""
    actions: list = []

    def fake_track(hass, entity_ids, action, **kwargs):
        actions.append((list(entity_ids), action))
        return lambda: None

    monkeypatch.setattr(avail, "async_track_state_change_event", fake_track)
    return actions


def test_transmitter_reconnect_reenables_cover(hass, monkeypatch):
    """Apres un redemarrage de HA (passerelle encore hors ligne), le volet
    est indisponible ; quand l'emetteur remonte, son changement d'etat
    reecrit l'entite -> de nouveau disponible."""
    actions = capture_tracker(monkeypatch)
    _, entity = build_cover(hass)
    asyncio.run(entity.async_added_to_hass())

    assert len(actions) == 1
    entity_ids, action = actions[0]
    assert entity_ids == [TRANSMITTER]

    hass.states.set(TRANSMITTER, SimpleNamespace(state="unavailable"))
    assert entity.available is False

    hass.states.set(TRANSMITTER, SimpleNamespace(state="on"))
    action(SimpleNamespace(data={}))
    entity.async_write_ha_state.assert_called()
    assert entity.available is True


def test_button_subscribes_and_recovers(hass, monkeypatch):
    """Root cause des boutons grises : le bouton ne reecrit jamais son etat
    seul. Le mixin s'abonne a l'emetteur des son ajout -> le bouton redevient
    actif des que la passerelle remonte."""
    actions = capture_tracker(monkeypatch)
    hass.data.setdefault("rts_gateway", {})["rolling_codes"] = RollingCodeStore(hass)
    entry = make_entry([make_device()])
    added = []
    asyncio.run(
        button_module.async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    )
    btn = added[0]
    prepare_entity(btn, "button.test_my_position")
    asyncio.run(btn.async_added_to_hass())

    assert len(actions) == 1
    action = actions[0][1]

    hass.states.set(TRANSMITTER, SimpleNamespace(state="unavailable"))
    assert btn.available is False  # grise au demarrage

    hass.states.set(TRANSMITTER, SimpleNamespace(state="on"))
    action(SimpleNamespace(data={}))
    btn.async_write_ha_state.assert_called()
    assert btn.available is True


def test_unsubscribe_on_remove(hass, monkeypatch):
    """Le desabonnement est fait au retrait de l'entite."""
    removed: list[bool] = []

    def fake_track(hass, entity_ids, action, **kwargs):
        def unsub():
            removed.append(True)

        return unsub

    monkeypatch.setattr(avail, "async_track_state_change_event", fake_track)
    _, entity = build_cover(hass)
    asyncio.run(entity.async_added_to_hass())
    asyncio.run(entity.async_will_remove_from_hass())
    assert removed == [True]


def test_no_subscription_without_transmitter(hass, monkeypatch):
    """Sans emetteur configure, aucun abonnement."""
    actions = capture_tracker(monkeypatch)
    _, entity = build_cover(hass)
    entity._transmitter_id = ""
    asyncio.run(entity.async_added_to_hass())
    assert actions == []
