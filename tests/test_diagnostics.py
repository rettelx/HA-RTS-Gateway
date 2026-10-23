"""Tests for the diagnostics support."""

from __future__ import annotations

import asyncio

from conftest import make_runtime_data
from fakes import FakeConfigEntry, make_device, prepare_entity

from custom_components.somfy_rts.const import DOMAIN
from custom_components.somfy_rts.cover import SomfyRTSCover
from custom_components.somfy_rts.diagnostics import async_get_config_entry_diagnostics
from custom_components.somfy_rts.rolling_code import RollingCodeStore


def test_config_entry_diagnostics(hass):
    device = make_device(address="12345678")
    entry = FakeConfigEntry(
        data={
            "devices": [device],
            "sensors": [],
            "transmitter_id": "radio_frequency.test_radio",
            "enable_rx": True,
        },
        runtime_data=make_runtime_data(),
    )
    hass.config_entries.entries.append(entry)
    entry_data = hass.data.setdefault(DOMAIN, {})
    store = RollingCodeStore(hass)
    entry_data["rolling_codes"] = store

    async def scenario():
        await store.async_get_next(12345678)  # counter -> 2
        cover = SomfyRTSCover(hass, entry, device)
        prepare_entity(cover, "cover.test")
        await cover.async_added_to_hass()

        diag = await async_get_config_entry_diagnostics(hass, entry)
        assert diag["transmitter"] == "radio_frequency.test_radio"
        assert diag["devices"][0]["address"] == "12345678"
        assert diag["rolling_codes"]["12345678"] == {"counter": 2}
        assert diag["entities"]["cover.test"] == "SomfyRTSCover"

    asyncio.run(scenario())
