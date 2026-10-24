"""Tests for the rolling code store."""

from __future__ import annotations

import asyncio

from custom_components.rts_gateway.rolling_code import RollingCodeStore


def test_get_next_increments_and_persists(hass, fake_store):
    async def scenario():
        store = RollingCodeStore(hass)
        assert await store.async_get_next(42) == 1
        assert await store.async_get_next(42) == 2
        assert await store.async_get(42) == 3  # peek without consuming
        assert await store.async_get_next(42) == 3

        # A new store instance reads the persisted counter.
        store2 = RollingCodeStore(hass)
        assert await store2.async_get(42) == 4

        # Addresses are independent.
        assert await store.async_get_next(43) == 1

    asyncio.run(scenario())


def test_get_returns_default_for_unknown_address(hass, fake_store):
    async def scenario():
        store = RollingCodeStore(hass)
        assert await store.async_get(99) == 1

    asyncio.run(scenario())


def test_set_and_remove(hass, fake_store):
    async def scenario():
        store = RollingCodeStore(hass)
        await store.async_set(42, 1000)
        assert await store.async_get(42) == 1000
        assert await store.async_get_next(42) == 1000

        await store.async_remove(42)
        assert await store.async_get(42) == 1

    asyncio.run(scenario())


def test_corrupted_storage_is_reset(hass, fake_store):
    async def scenario():
        fake_store._bank["rts_gateway.rolling_codes"] = "not-a-dict"
        store = RollingCodeStore(hass)
        assert await store.async_get(42) == 1
        await store.async_set(42, 10)
        assert fake_store._bank["rts_gateway.rolling_codes"]["devices"]["42"] == {"counter": 10}

    asyncio.run(scenario())
