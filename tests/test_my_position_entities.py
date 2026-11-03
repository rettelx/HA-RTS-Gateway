"""Tests for the My-position sensor and slider (number) entities."""

from __future__ import annotations

import asyncio
import copy

from conftest import make_runtime_data
from fakes import (
    FakeConfigEntry,
    make_device,
    persisted_entry_data,
    prepare_entity,
    restart_cover,
)

from custom_components.rts_gateway import number as number_module
from custom_components.rts_gateway import sensor as sensor_module
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


def setup_entities(hass, devices, module):
    hass.data.setdefault("rts_gateway", {})["rolling_codes"] = RollingCodeStore(hass)
    entry = make_entry(devices)
    added = []
    asyncio.run(module.async_setup_entry(hass, entry, lambda ents: added.extend(ents)))
    return entry, added


def add_cover(hass, entry, device, entity_id="cover.test"):
    cover = SomfyRTSCover(hass, entry, device)
    prepare_entity(cover, entity_id)
    asyncio.run(cover.async_added_to_hass())
    return cover


def add_sensor(hass, entry, sensor_entity, entity_id="sensor.test_my_position"):
    prepare_entity(sensor_entity, entity_id)
    asyncio.run(sensor_entity.async_added_to_hass())
    return sensor_entity


def test_sensor_created_per_cover_only(hass):
    """Un capteur par volet ; pas pour les prises."""
    _, added = setup_entities(
        hass,
        [make_device(device_type="roller_shutter"), make_device(device_type="socket")],
        sensor_module,
    )
    assert len(added) == 1
    assert added[0].unique_id == "rts_gateway_12345678_my_position"
    assert added[0].translation_key == "my_position"
    assert added[0].native_unit_of_measurement == "%"


def test_sensor_reads_cover_my_position(hass):
    """La valeur vient du cover vivant ; repli sur la config sinon."""
    devices = [make_device(my_position=30)]
    entry, added = setup_entities(hass, devices, sensor_module)
    sensor = add_sensor(hass, entry, added[0])

    # Sans cover enregistre : repli sur la config de l'entree.
    assert sensor.native_value == 30.0
    assert sensor.extra_state_attributes["simulate_my"] is True

    cover = add_cover(hass, entry, devices[0])
    assert sensor.native_value == 30.0

    cover.async_set_my_position(55)
    assert sensor.native_value == 55.0


def test_sensor_unknown_when_my_position_unset(hass):
    """Pas de position MY configuree -> etat inconnu."""
    devices = [make_device()]
    entry, added = setup_entities(hass, devices, sensor_module)
    sensor = add_sensor(hass, entry, added[0])
    add_cover(hass, entry, devices[0])
    assert sensor.native_value is None


def test_number_created_only_when_simulate_my(hass):
    """Slider uniquement pour les volets en MY simule (defaut)."""
    _, added = setup_entities(
        hass,
        [
            make_device(address="11111111"),
            make_device(address="22222222", simulate_my=False),
            make_device(address="33333333", device_type="socket"),
        ],
        number_module,
    )
    assert len(added) == 1
    assert added[0].unique_id == "rts_gateway_11111111_set_my_position"
    assert added[0].translation_key == "set_my_position"
    assert added[0].mode == number_module.NumberMode.SLIDER
    assert added[0].native_min_value == 0
    assert added[0].native_max_value == 100
    assert added[0].native_unit_of_measurement == "%"


def setup_both(hass, devices):
    """Set up sensor + number platforms on the same entry."""
    hass.data.setdefault("rts_gateway", {})["rolling_codes"] = RollingCodeStore(hass)
    entry = make_entry(devices)
    sensors, numbers = [], []
    asyncio.run(sensor_module.async_setup_entry(hass, entry, lambda e: sensors.extend(e)))
    asyncio.run(number_module.async_setup_entry(hass, entry, lambda e: numbers.extend(e)))
    return entry, sensors, numbers


def test_number_set_value_updates_cover_and_persists(hass):
    """Le slider ecrit la position sur le cover et dans l'entree."""
    devices = [make_device(my_position=30)]
    entry, sensors, numbers = setup_both(hass, devices)
    sensor = add_sensor(hass, entry, sensors[0])
    cover = add_cover(hass, entry, devices[0])
    number = numbers[0]
    prepare_entity(number, "number.test_set_my_position")

    assert number.native_value == 30.0

    asyncio.run(number.async_set_native_value(45))

    assert cover._my_position == 45
    assert entry.data["devices"][0]["my_position"] == 45  # persiste
    assert sensor.native_value == 45.0  # capteur rafraichi via le cover
    assert number.native_value == 45.0


def test_cover_notifies_sensor_on_my_position_change(hass):
    """async_set_my_position declenche l'ecriture du capteur enregistre."""
    devices = [make_device(my_position=30)]
    entry, sensors = setup_entities(hass, devices, sensor_module)
    sensor = add_sensor(hass, entry, sensors[0])
    cover = add_cover(hass, entry, devices[0])

    cover.async_set_my_position(70)
    sensor.async_write_ha_state.assert_called()
    assert sensor.native_value == 70.0


def test_memorize_from_rx_refreshes_sensor_and_persists(hass, no_timer):
    """Un appui long MY recu met a jour capteur + entree (chemin persistant)."""
    devices = [make_device(my_position=30, simulate_my=False)]
    entry, sensors, _ = setup_both(hass, devices)
    sensor = add_sensor(hass, entry, sensors[0])
    cover = add_cover(hass, entry, devices[0])
    cover.async_set_current_position(80)

    cover._memorize_my_position_from_rx(80)

    assert cover._my_position == 80
    assert entry.data["devices"][0]["my_position"] == 80
    assert sensor.native_value == 80.0
    sensor.async_write_ha_state.assert_called()


def test_slider_value_survives_ha_restart(hass):
    """Le slider ecrit dans le config entry sauvegarde (async_update_entry)
    : la valeur est retrouvee apres un redemarrage de HA."""
    devices = [make_device(my_position=30)]
    entry, _, numbers = setup_both(hass, devices)
    add_cover(hass, entry, devices[0])
    number = numbers[0]
    prepare_entity(number, "number.test_set_my_position")

    original = copy.deepcopy(entry.data)  # etat disque avant ecriture
    asyncio.run(number.async_set_native_value(45))

    disk = persisted_entry_data(hass, entry, original)
    assert disk["devices"][0]["my_position"] == 45
    assert restart_cover(hass, disk)._my_position == 45


def test_rx_memorize_survives_ha_restart(hass, no_timer):
    """Un appui long MY recu d'une telecommande persiste la favorite
    dans le config entry : retrouvee apres redemarrage."""
    devices = [make_device(my_position=30, simulate_my=False)]
    entry, _, _ = setup_both(hass, devices)
    cover = add_cover(hass, entry, devices[0])
    cover.async_set_current_position(80)

    original = copy.deepcopy(entry.data)
    cover._memorize_my_position_from_rx(80)

    disk = persisted_entry_data(hass, entry, original)
    assert disk["devices"][0]["my_position"] == 80
    assert restart_cover(hass, disk)._my_position == 80


def test_sensor_and_number_available_without_transmitter(hass):
    """Entites de pure config : disponibles meme quand la passerelle RF est
    hors ligne (aucun etat d'emetteur) -- pas de mixin de disponibilite."""
    devices = [make_device(my_position=86)]
    entry, sensors, numbers = setup_both(hass, devices)
    assert hass.states.get("radio_frequency.test_radio") is None  # gateway down

    sensor = add_sensor(hass, entry, sensors[0])
    assert sensor.available is True
    # Cover non enregistre -> repli sur la config de l'entree.
    assert sensor.native_value == 86.0

    number = numbers[0]
    prepare_entity(number, "number.test_set_my_position")
    assert number.available is True
    assert number.native_value == 86.0


def test_my_position_read_from_persisted_config_after_restart(hass):
    """Apres un redemarrage de HA, le sensor lit la valeur persistee dans le
    config entry, meme si le cover n'est pas encore enregistre (repli config)."""
    devices = [make_device(my_position=86)]
    entry, _, _ = setup_both(hass, devices)
    add_cover(hass, entry, devices[0])

    # Redemarrage : nouvelle entree reconstruite depuis les donnees disque.
    disk = copy.deepcopy(entry.data)
    assert disk["devices"][0]["my_position"] == 86
    restarted = FakeConfigEntry(data=disk, runtime_data=make_runtime_data())

    added = []
    asyncio.run(
        sensor_module.async_setup_entry(hass, restarted, lambda e: added.extend(e))
    )
    sensor = added[0]
    prepare_entity(sensor, "sensor.restarted_my_position")
    assert restarted.runtime_data.entities == {}  # cover pas encore la
    assert sensor.native_value == 86.0
