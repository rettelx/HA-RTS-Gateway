"""Tests for the switch platform."""

from __future__ import annotations

import asyncio

from fakes import FakeConfigEntry, make_device, prepare_entity

from custom_components.somfy_rts.const import SomfyCommand
from custom_components.somfy_rts.rolling_code import RollingCodeStore
from custom_components.somfy_rts.switch import SomfyRTSSwitch


def build_switch(hass, **device_overrides):
    device = make_device(device_type="socket", **device_overrides)
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
    hass.data.setdefault("somfy_rts", {})["rolling_codes"] = RollingCodeStore(hass)
    entity = SomfyRTSSwitch(hass, entry, device)
    prepare_entity(entity, "switch.test")
    return entity


def test_setup_only_creates_sockets(hass):
    from custom_components.somfy_rts import switch

    hass.data["somfy_rts"] = {"rolling_codes": RollingCodeStore(hass)}
    entry = FakeConfigEntry(
        data={
            "devices": [
                make_device(device_type="socket"),
                make_device(device_type="light"),
                make_device(device_type="roller_shutter"),
            ],
            "sensors": [],
        }
    )
    added = []
    asyncio.run(switch.async_setup_entry(hass, entry, lambda entities: added.extend(entities)))
    assert len(added) == 1
    assert isinstance(added[0], SomfyRTSSwitch)


def test_turn_on_off_toggle(hass, fake_rf):
    async def scenario():
        entity = build_switch(hass)
        await entity.async_turn_on()
        assert fake_rf.sent[-1]["command"] == "MY"
        assert entity.is_on is True

        # Already on: no duplicate toggle.
        await entity.async_turn_on()
        assert len(fake_rf.sent) == 1

        await entity.async_turn_off()
        assert fake_rf.sent[-1]["command"] == "MY"
        assert entity.is_on is False

        await entity.async_turn_off()
        assert len(fake_rf.sent) == 2

    asyncio.run(scenario())


def test_toggle_command_is_configurable(hass, fake_rf):
    async def scenario():
        entity = build_switch(hass, toggle_command="UP")
        await entity.async_turn_on()
        assert fake_rf.sent[-1]["command"] == "UP"

    asyncio.run(scenario())


def test_rx_toggle_flips_state(hass):
    entity = build_switch(hass, toggle_command="UP")

    entity.async_update_from_rx(SomfyCommand.UP, 1, "remote", 0)
    assert entity.is_on is True
    entity.async_update_from_rx(SomfyCommand.UP, 2, "remote", 0)
    assert entity.is_on is False

    # Other commands do not toggle.
    entity.async_update_from_rx(SomfyCommand.DOWN, 3, "remote", 0)
    assert entity.is_on is False
    assert entity.extra_state_attributes["last_rx_device"] == "remote"


def test_update_linked_remotes(hass):
    """Regression: linked remote updates must work for switches too."""
    entity = build_switch(hass, linked_remote_addresses=[])
    entity.async_update_linked_remotes(["00000001"])
    assert entity.linked_remote_addresses_int == [1]
    assert entity._device_config["linked_remote_addresses"] == ["00000001"]


def test_switch_restore_and_remove(hass):
    """Restore du dernier etat + desenregistrement du registre."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    entity = build_switch(hass)
    last = SimpleNamespace(state="on")
    entity.async_get_last_state = AsyncMock(return_value=last)

    async def scenario():
        await entity.async_added_to_hass()
        registry = entity._entry.runtime_data.entities
        assert registry["switch.test"] is entity
        assert entity.is_on is True

        await entity.async_will_remove_from_hass()
        assert "switch.test" not in registry

    asyncio.run(scenario())
