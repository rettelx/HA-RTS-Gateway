"""Tests for the button platform."""

from __future__ import annotations

import asyncio

from conftest import make_runtime_data
from fakes import FakeConfigEntry, make_device, prepare_entity

from custom_components.rts_gateway import button
from custom_components.rts_gateway.cover import SomfyRTSCover
from custom_components.rts_gateway.rolling_code import RollingCodeStore


def make_entry(devices):
    return FakeConfigEntry(
        data={
            "devices": devices,
            "sensors": [],
            "transmitter_id": "radio_frequency.test_radio",
            "enable_rx": True,
        },
        runtime_data=make_runtime_data(),
    )


def setup_button_entities(hass, devices):
    hass.data.setdefault("rts_gateway", {})[
        "rolling_codes"
    ] = RollingCodeStore(hass)
    entry = make_entry(devices)
    added = []
    asyncio.run(
        button.async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    )
    return entry, added


def test_setup_creates_buttons_per_type(hass):
    _, added = setup_button_entities(
        hass, [make_device(device_type="roller_shutter"), make_device(device_type="socket")]
    )
    keys = sorted(b.translation_key for b in added)
    # Volet : MY + recalage. Prise : aucun bouton (services only).
    assert keys == ["goto_my_position", "recalibrate"]
    # Chaque entite a un unique_id stable, derive de l'adresse et de la
    # cle de traduction (checklist entity-unique-id). Regressions: un
    # unique_id lu sur la classe (objet property sur HA >= 2026.10)
    # changeait a chaque redemarrage et collidait entre les 2 boutons.
    assert {b.unique_id for b in added} == {
        "rts_gateway_12345678_goto_my_position",
        "rts_gateway_12345678_recalibrate",
    }


def test_my_and_recal_buttons_call_the_cover(hass, fake_rf, no_timer):
    devices = [make_device(address="12345678", my_position=30)]
    entry, added = setup_button_entities(hass, devices)

    cover = SomfyRTSCover(hass, entry, devices[0])
    entry.runtime_data = make_runtime_data()
    prepare_entity(cover, "cover.test")
    asyncio.run(cover.async_added_to_hass())

    my_button = next(b for b in added if b.translation_key == "goto_my_position")
    asyncio.run(my_button.async_press())
    # Position favorite 30 depuis 100 : descente.
    assert fake_rf.sent[-1]["command"] == "DOWN"

    recal_button = next(b for b in added if b.translation_key == "recalibrate")
    asyncio.run(recal_button.async_press())
    assert cover._recal_task is not None
    asyncio.run(_cancel(cover))


async def _cancel(cover):
    cover._recal_task.cancel()
    await asyncio.sleep(0)


def test_buttons_survive_missing_cover(hass, fake_rf):
    _, added = setup_button_entities(hass, [make_device(address="12345678")])
    my_button = next(b for b in added if b.translation_key == "goto_my_position")
    # Pas d'entite cover enregistree : l'appui ne leve pas.
    asyncio.run(my_button.async_press())
    assert fake_rf.sent == []


def test_my_button_without_position_does_nothing(hass, fake_rf):
    """Bouton MY sans position configuree : aucune commande."""
    devices = [make_device()]
    entry, added = setup_button_entities(hass, devices)
    cover = SomfyRTSCover(hass, entry, devices[0])
    prepare_entity(cover, "cover.test")
    asyncio.run(cover.async_added_to_hass())

    my_button = next(
        b for b in added if b.translation_key == "goto_my_position"
    )
    asyncio.run(my_button.async_press())
    assert fake_rf.sent == []


def test_recal_button_without_cover_does_nothing(hass, fake_rf):
    """Bouton Recalage sans entite cover : silencieux."""
    _, added = setup_button_entities(hass, [make_device()])
    recal_button = next(
        b for b in added if b.translation_key == "recalibrate"
    )
    asyncio.run(recal_button.async_press())
    assert fake_rf.sent == []
