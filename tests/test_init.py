"""Tests for the integration setup/unload lifecycle (__init__.py)."""

from __future__ import annotations

import asyncio

import pytest
from fakes import FakeConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.rts_gateway import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.rts_gateway.const import DOMAIN, RX_EVENT
from custom_components.rts_gateway.rolling_code import RollingCodeStore


def make_entry(entry_id="entry1", transmitter="radio_frequency.test_radio", enable_rx=True):
    return FakeConfigEntry(
        entry_id=entry_id,
        data={
            "transmitter_id": transmitter,
            "enable_rx": enable_rx,
            "devices": [],
            "sensors": [],
        },
    )


def test_async_setup(hass):
    async def scenario():
        assert await async_setup(hass, None) is True
        assert DOMAIN in hass.data
        assert isinstance(hass.data[DOMAIN]["rolling_codes"], RollingCodeStore)
        assert ("rts_gateway", "link_remote") in hass.services.registered
        assert ("rts_gateway", "unlink_remote") in hass.services.registered

    asyncio.run(scenario())


def test_setup_entry_requires_known_transmitter(hass, fake_rf):
    async def scenario():
        assert await async_setup(hass, None) is True
        entry = make_entry(transmitter="radio_frequency.unknown")
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

    asyncio.run(scenario())


def test_setup_entry_removes_stale_devices(hass, fake_rf):
    """Les appareils dont l'adresse a quitte l'entree sont purges
    du registre au rechargement ; les autres sont conserves."""
    async def scenario():
        assert await async_setup(hass, None) is True
        entry = make_entry()
        entry.data["devices"] = [{"address": "11111111", "name": "Volet"}]
        entry.data["sensors"] = [{"address": "22222222", "name": "Soleil"}]
        hass.config_entries.entries.append(entry)

        registry = hass.data["device_registry"]
        stale = registry.async_get_or_create(
            entry.entry_id, {(DOMAIN, "99999999")}
        )
        stale_sensor = registry.async_get_or_create(
            entry.entry_id, {(DOMAIN, "sensor_33333333")}
        )
        kept = registry.async_get_or_create(
            entry.entry_id, {(DOMAIN, "11111111")}
        )
        other_entry = registry.async_get_or_create(
            "other_entry", {(DOMAIN, "99999999")}
        )

        assert await async_setup_entry(hass, entry) is True

        assert registry.removed == [stale.id, stale_sensor.id]
        assert kept.id in registry._devices._items
        assert other_entry.id in registry._devices._items

    asyncio.run(scenario())


def test_setup_entry_forwards_platforms_and_starts_rx(hass, fake_rf):
    async def scenario():
        assert await async_setup(hass, None) is True
        entry = make_entry()
        hass.config_entries.entries.append(entry)
        assert await async_setup_entry(hass, entry) is True

        runtime = entry.runtime_data
        assert runtime.entities == {}
        assert runtime.sensors == {}
        assert entry.entry_id in hass.data[DOMAIN]["loaded_entries"]
        assert len(hass.config_entries.forwarded) == 1
        assert entry.update_listeners  # reload listener registered
        assert "rx_handler" in hass.data[DOMAIN]
        assert RX_EVENT in hass.bus.listeners

    asyncio.run(scenario())


def test_setup_entry_without_rx(hass, fake_rf):
    async def scenario():
        assert await async_setup(hass, None) is True
        entry = make_entry(enable_rx=False)
        assert await async_setup_entry(hass, entry) is True
        assert "rx_handler" not in hass.data[DOMAIN]

    asyncio.run(scenario())


def test_options_override_enable_rx():
    from custom_components.rts_gateway import _rx_enabled

    assert _rx_enabled(make_entry(enable_rx=True)) is True
    assert _rx_enabled(make_entry(enable_rx=False)) is False

    entry = make_entry(enable_rx=True)
    entry.options["enable_rx"] = False
    assert _rx_enabled(entry) is False

    entry = make_entry(enable_rx=False)
    entry.options["enable_rx"] = True
    assert _rx_enabled(entry) is True


def test_update_listener_reloads_entry(hass, fake_rf):
    async def scenario():
        assert await async_setup(hass, None) is True
        entry = make_entry()
        await async_setup_entry(hass, entry)

        (listener,) = entry.update_listeners
        await listener(hass, entry)
        assert entry.entry_id in hass.config_entries.reloads

    asyncio.run(scenario())


def test_unload_last_entry_stops_handler_and_services(hass, fake_rf):
    async def scenario():
        assert await async_setup(hass, None) is True
        entry = make_entry()
        hass.config_entries.entries.append(entry)
        await async_setup_entry(hass, entry)

        assert await async_unload_entry(hass, entry) is True
        assert entry.entry_id not in hass.data[DOMAIN]
        assert "rx_handler" not in hass.data[DOMAIN]
        assert RX_EVENT not in hass.bus.listeners
        assert not hass.services.registered  # last entry: services removed

    asyncio.run(scenario())


def test_reloading_the_only_entry_registers_services_again(hass, fake_rf):
    """Regression: unload removes the services of the last loaded entry;
    the next setup must re-register them (async_setup runs only once)."""
    async def scenario():
        assert await async_setup(hass, None) is True
        entry = make_entry()
        hass.config_entries.entries.append(entry)
        await async_setup_entry(hass, entry)
        await async_unload_entry(hass, entry)
        assert not hass.services.registered

        assert await async_setup_entry(hass, entry) is True
        assert ("rts_gateway", "link_remote") in hass.services.registered
        assert ("rts_gateway", "unlink_remote") in hass.services.registered
        assert "rx_handler" in hass.data[DOMAIN]

    asyncio.run(scenario())


def test_unload_keeps_handler_while_other_entries_listen(hass, fake_rf):
    async def scenario():
        assert await async_setup(hass, None) is True
        first = make_entry("entry1")
        second = make_entry("entry2", enable_rx=True)
        hass.config_entries.entries.extend([first, second])
        await async_setup_entry(hass, first)
        await async_setup_entry(hass, second)

        assert await async_unload_entry(hass, first) is True
        assert "rx_handler" in hass.data[DOMAIN]
        assert ("rts_gateway", "link_remote") in hass.services.registered

        # Unloading the last RX-enabled entry stops the handler.
        assert await async_unload_entry(hass, second) is True
        assert "rx_handler" not in hass.data[DOMAIN]
        assert not hass.services.registered

    asyncio.run(scenario())


def test_setup_entry_defensive_setup(hass, fake_rf):
    """async_setup_entry fonctionne meme sans async_setup prealable."""
    async def scenario():
        entry = make_entry()
        hass.config_entries.entries.append(entry)
        assert DOMAIN not in hass.data
        assert await async_setup_entry(hass, entry) is True
        assert "rolling_codes" in hass.data[DOMAIN]
        assert "rx_handler" in hass.data[DOMAIN]

    asyncio.run(scenario())


def test_unload_failure_returns_false(hass, fake_rf, monkeypatch):
    """Echec du dechargement des plateformes -> False."""
    async def scenario():
        assert await async_setup(hass, None) is True
        entry = make_entry()
        hass.config_entries.entries.append(entry)
        assert await async_setup_entry(hass, entry) is True

        async def failing_unload(entry, platforms):
            return False

        monkeypatch.setattr(
            hass.config_entries, "async_unload_platforms", failing_unload
        )
        assert await async_unload_entry(hass, entry) is False
        # L'entree reste marquee chargee.
        assert entry.entry_id in hass.data[DOMAIN]["loaded_entries"]

    asyncio.run(scenario())
