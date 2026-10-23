"""Tests for the light platform."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock

from fakes import FakeConfigEntry, make_device, prepare_entity
from homeassistant.components.light import ColorMode

from custom_components.somfy_rts import light as light_module
from custom_components.somfy_rts.const import SomfyCommand
from custom_components.somfy_rts.light import SomfyRTSLight
from custom_components.somfy_rts.rolling_code import RollingCodeStore

STEP_INTERVAL = 0.01


def build_light(hass, **device_overrides):
    device_overrides.setdefault("protocol", "rts_80bit")
    device = make_device(device_type="light", **device_overrides)
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
    entity = SomfyRTSLight(hass, entry, device)
    prepare_entity(entity, "light.test")
    return entity


def test_color_modes(hass):
    dimmer = build_light(hass)  # constructor defaults to 80-bit
    assert dimmer.color_mode == ColorMode.BRIGHTNESS
    assert dimmer.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert dimmer.brightness == 255

    simple = build_light(hass, protocol="rts_56bit")
    assert simple.color_mode == ColorMode.ONOFF
    assert simple.supported_color_modes == {ColorMode.ONOFF}
    assert simple.brightness is None


def test_turn_on_off_toggle(hass, fake_rf):
    async def scenario():
        entity = build_light(hass)
        await entity.async_turn_on()
        assert fake_rf.sent[-1]["command"] == "MY"
        assert entity.is_on is True

        # Already on: no extra toggle.
        await entity.async_turn_on()
        assert len(fake_rf.sent) == 1

        await entity.async_turn_off()
        assert fake_rf.sent[-1]["command"] == "MY"
        assert entity.is_on is False

        # Already off: no extra toggle.
        await entity.async_turn_off()
        assert len(fake_rf.sent) == 2

    asyncio.run(scenario())


def test_toggle_command_is_configurable(hass, fake_rf):
    async def scenario():
        entity = build_light(hass, toggle_command="DOWN")
        await entity.async_turn_on()
        assert fake_rf.sent[-1]["command"] == "DOWN"

    asyncio.run(scenario())


def test_dim_runs_to_target(hass, fake_rf, monkeypatch):
    monkeypatch.setattr(light_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)

    async def scenario():
        entity = build_light(hass, brightness_step_percent=5)
        await entity.async_turn_on(brightness=50)
        await entity._dim_task

        assert entity.brightness == 50
        commands = [s["command"] for s in fake_rf.sent[1:]]  # skip the toggle
        assert commands == ["STEP_DOWN"] * 17  # (255-50) / (5% of 255)

    asyncio.run(scenario())


def test_dim_stop_keeps_partial_estimate(hass, fake_rf, monkeypatch):
    """Regression: stopping mid-dim must not snap to the target."""
    monkeypatch.setattr(light_module, "TILT_STEP_INTERVAL_S", STEP_INTERVAL)

    async def scenario():
        entity = build_light(hass, brightness_step_percent=5)
        await entity.async_turn_on(brightness=0)
        await asyncio.sleep(STEP_INTERVAL * 4)
        task = entity._dim_task
        entity._cancel_dim_task()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        brightness = entity.brightness
        assert 0 < brightness < 255
        # 5% of 255 = 12 per sent step frame, minus the initial toggle.
        assert brightness == 255 - 12 * (len(fake_rf.sent) - 1)

    asyncio.run(scenario())


def test_brightness_requires_80bit(hass, fake_rf):
    async def scenario():
        entity = build_light(hass, protocol="rts_56bit")
        await entity.async_turn_on(brightness=50)
        # Only the toggle was sent: no step frames.
        assert len(fake_rf.sent) == 1
        assert entity._dim_task is None

    asyncio.run(scenario())


def test_restore_of_last_state(hass):
    async def scenario():
        from types import SimpleNamespace

        entity = build_light(hass)
        entity.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(state="on", attributes={"brightness": 100})
        )
        await entity.async_added_to_hass()
        assert entity.is_on is True
        assert entity.brightness == 100

    asyncio.run(scenario())


def test_rx_updates(hass):
    entity = build_light(hass)
    entity._brightness = 100

    entity.async_update_from_rx(SomfyCommand.MY, 1, "remote", 0)
    assert entity.is_on is True
    entity.async_update_from_rx(SomfyCommand.MY, 2, "remote", 0)
    assert entity.is_on is False

    entity.async_update_from_rx(SomfyCommand.STEP_UP, 3, "remote", 0)
    assert entity.brightness == 112  # + 5% of 255 (12)
    entity.async_update_from_rx(SomfyCommand.STEP_DOWN, 4, "remote", 0)
    assert entity.brightness == 100

    # An unrelated command only records the device.
    entity.async_update_from_rx(SomfyCommand.PROG, 5, "remote", 0)
    assert entity.extra_state_attributes["last_rx_device"] == "remote"


def test_update_linked_remotes(hass):
    """Regression: linked remote updates must work for lights too."""
    entity = build_light(hass, linked_remote_addresses=[])
    entity.async_update_linked_remotes(["00000001"])
    assert entity.linked_remote_addresses_int == [1]
    assert entity._device_config["linked_remote_addresses"] == ["00000001"]


def test_light_setup_entry(hass):
    """Le setup ne cree que les types eclairage."""
    from conftest import make_runtime_data

    from custom_components.somfy_rts import light as light_module

    device = make_device(device_type="light", protocol="rts_80bit")
    entry = FakeConfigEntry(
        data={
            "devices": [device, make_device(device_type="socket")],
            "sensors": [],
            "transmitter_id": "radio_frequency.test_radio",
        },
        runtime_data=make_runtime_data(),
    )
    hass.data.setdefault("somfy_rts", {})[
        "rolling_codes"
    ] = RollingCodeStore(hass)
    added = []

    async def scenario():
        await light_module.async_setup_entry(
            hass, entry, lambda ents: added.extend(ents)
        )

    asyncio.run(scenario())
    assert len(added) == 1
    assert isinstance(added[0], light_module.SomfyRTSLight)


def test_light_will_remove_cancels_dim(hass, fake_rf, no_timer):
    """will_remove desenregistre et annule un variateur en cours."""

    async def scenario():
        light = build_light(hass, protocol="rts_80bit")
        await light.async_added_to_hass()
        registry = light._entry.runtime_data.entities
        assert registry["light.test"] is light

        # Variateur en cours (tache de dimming).
        light._dim_task = asyncio.ensure_future(asyncio.sleep(50))
        task = light._dim_task
        await light.async_will_remove_from_hass()
        assert "light.test" not in registry
        await asyncio.sleep(0)
        assert task.cancelled()


    asyncio.run(scenario())


def test_step_dim_zero_delta_is_noop(hass, fake_rf, no_timer):
    """Aucun ecart de luminosite : aucune trame."""
    async def scenario():
        light = build_light(hass, protocol="rts_80bit")
        light._brightness = 150
        await light._async_dim_to_target(150)
        assert fake_rf.sent == []


    asyncio.run(scenario())
