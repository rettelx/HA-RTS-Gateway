"""Tests for the binary sensor platform (sun/wind sensors)."""

from __future__ import annotations

import asyncio

from conftest import make_runtime_data
from fakes import FakeConfigEntry, make_sensor_config, prepare_entity

from custom_components.rts_gateway import binary_sensor
from custom_components.rts_gateway.binary_sensor import (
    KIND_SUN,
    KIND_WIND,
    SomfyRTSSensor,
    parse_sensor_state,
)


def build_sensor(hass, kind=KIND_WIND):
    hass.data.setdefault("rts_gateway", {})
    entry = FakeConfigEntry(
        data={"devices": [], "sensors": [make_sensor_config()]},
        runtime_data=make_runtime_data(),
    )
    sensor = SomfyRTSSensor(hass, entry, make_sensor_config(), kind)
    prepare_entity(sensor, f"binary_sensor.test_{kind}")
    return sensor


def test_parse_sensor_state():
    assert parse_sensor_state(0x00) == (False, False)
    assert parse_sensor_state(0x01) == (True, False)  # bit 0 = wind
    assert parse_sensor_state(0x02) == (False, True)  # bit 1 = sun
    assert parse_sensor_state(0x03) == (True, True)


def test_setup_creates_wind_and_sun_entities(hass):
    entry = FakeConfigEntry(
        data={"devices": [], "sensors": [make_sensor_config()]},
        runtime_data=make_runtime_data(),
    )
    added = []
    asyncio.run(binary_sensor.async_setup_entry(hass, entry, lambda entities: added.extend(entities)))
    assert len(added) == 2
    kinds = {s._kind for s in added}
    assert kinds == {KIND_WIND, KIND_SUN}


def test_sensor_attributes(hass):
    wind = build_sensor(hass, KIND_WIND)
    assert wind.translation_key == "wind"
    assert wind.icon is None  # icone portee par les traductions
    assert wind.is_on is False

    sun = build_sensor(hass, KIND_SUN)
    assert sun.translation_key == "sun"


def test_registry_lifecycle(hass):
    async def scenario():
        sensor = build_sensor(hass, KIND_WIND)
        await sensor.async_added_to_hass()
        registry = sensor._entry.runtime_data.sensors
        assert registry[(11112222, KIND_WIND)] is sensor

        await sensor.async_will_remove_from_hass()
        assert (11112222, KIND_WIND) not in registry

    asyncio.run(scenario())


def test_update_from_rx(hass):
    wind = build_sensor(hass, KIND_WIND)
    sun = build_sensor(hass, KIND_SUN)

    wind.async_update_from_rx(0x01, "esphome")
    assert wind.is_on is True
    assert sun.is_on is False
    assert wind.extra_state_attributes["last_payload"] == 0x01

    wind.async_update_from_rx(0x02, "esphome")
    sun.async_update_from_rx(0x02, "esphome")
    assert wind.is_on is False
    assert sun.is_on is True
    assert sun.extra_state_attributes["last_rx_device"] == "esphome"
