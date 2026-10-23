"""Tests for address_manager."""

from __future__ import annotations

import pytest
from fakes import FakeConfigEntry

from custom_components.somfy_rts import address_manager
from custom_components.somfy_rts.const import DOMAIN


def test_is_address_valid():
    assert address_manager.is_address_valid("00000000")
    assert address_manager.is_address_valid("12345678")
    assert address_manager.is_address_valid("16777215")
    assert not address_manager.is_address_valid("16777216")
    assert not address_manager.is_address_valid("1234567")
    assert not address_manager.is_address_valid("123456789")
    assert not address_manager.is_address_valid("1234567a")
    assert not address_manager.is_address_valid("")

    # missing: not a string / not digits
    assert not address_manager.is_address_valid(12345678)
    assert not address_manager.is_address_valid("abcdefgh")


def test_get_used_addresses(hass):
    entry = FakeConfigEntry(
        data={
            "devices": [
                {"address": "00000001"},
                {"address": "00000002"},
                {"address": "oops"},  # malformed: ignored
            ],
            "sensors": [{"address": "00000003"}],
        }
    )
    entry.domain = DOMAIN
    hass.config_entries.entries.append(entry)

    other = FakeConfigEntry(entry_id="other", domain="other_domain", data={})
    hass.config_entries.entries.append(other)

    assert address_manager.get_used_addresses(hass) == {1, 2, 3}


def test_is_address_used(hass):
    entry = FakeConfigEntry(data={"devices": [{"address": "00000001"}], "sensors": []})
    entry.domain = DOMAIN
    hass.config_entries.entries.append(entry)

    assert address_manager.is_address_used(hass, "00000001")
    assert not address_manager.is_address_used(hass, "00000002")
    # Non-numeric addresses fail closed (counted as used).
    assert address_manager.is_address_used(hass, "not-a-number")


def test_generate_address_format_and_uniqueness(hass):
    addresses = [address_manager.generate_address(hass) for _ in range(50)]
    assert all(len(a) == 8 and a.isdigit() for a in addresses)
    assert len(set(addresses)) == 50


def test_generate_address_skips_used(hass, monkeypatch):
    used = [5]

    def fake_randint(low, high):
        return used[0]

    monkeypatch.setattr(address_manager.random, "randint", fake_randint)
    assert address_manager.generate_address(hass) == "00000005"

    # Once the address is in use, the next draw must be different.
    entry = FakeConfigEntry(data={"devices": [{"address": "00000005"}], "sensors": []})
    entry.domain = DOMAIN
    hass.config_entries.entries.append(entry)

    draws = []

    def fake_randint_bis(low, high):
        draws.append(1)
        return 6 if len(draws) > 1 else 5

    monkeypatch.setattr(address_manager.random, "randint", fake_randint_bis)
    assert address_manager.generate_address(hass) == "00000006"


def test_generate_address_gives_up(hass, monkeypatch):
    entry = FakeConfigEntry(data={"devices": [{"address": "00000007"}], "sensors": []})
    hass.config_entries.entries.append(entry)

    calls = []

    def always_used(low, high):
        calls.append(1)
        return 7

    monkeypatch.setattr(address_manager.random, "randint", always_used)
    with pytest.raises(RuntimeError):
        address_manager.generate_address(hass)
    assert len(calls) == 100


def test_generated_addresses_avoid_group_range(hass):
    """Les adresses d'appareils generee ne tombent jamais dans la plage groupe."""
    import random

    from custom_components.somfy_rts import address_manager
    from custom_components.somfy_rts.const import (
        GROUP_ADDRESS_MAX,
        GROUP_ADDRESS_MIN,
    )

    # Force les deux branches (au-dessus / en-dessous de la plage groupe).
    for seed in range(200):
        random.seed(seed)
        address = address_manager.generate_address(hass)
        value = int(address)
        assert not GROUP_ADDRESS_MIN <= value <= GROUP_ADDRESS_MAX
        assert address_manager.is_address_valid(address)


def test_used_addresses_skips_invalid_sensor(hass):
    """Un capteur avec une adresse non numerique est ignore."""
    from fakes import FakeConfigEntry

    from custom_components.somfy_rts import address_manager

    entry = FakeConfigEntry(
        data={
            "devices": [],
            "sensors": [{"name": "Capteur", "address": "not-a-number"}],
        }
    )
    hass.config_entries.entries.append(entry)
    assert address_manager.get_used_addresses(hass) == set()
