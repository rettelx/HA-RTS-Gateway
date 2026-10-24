"""Address management for RTS virtual remotes.

RTS addresses are 24-bit. This project uses the 8-digit decimal
notation (``00000000`` to ``16777215``).

Addresses in use are derived from existing config entries, so they survive
restarts without extra storage.
"""

from __future__ import annotations

import random

from homeassistant.core import HomeAssistant

from .const import (
    DEVICE_ADDRESS_MAX,
    DEVICE_ADDRESS_MIN,
    DOMAIN,
    GROUP_ADDRESS_MAX,
    GROUP_ADDRESS_MIN,
)


def is_address_valid(address: str, group: bool = False) -> bool:
    """Return whether an address is a valid 8-digit decimal RTS address.

    Group addresses live in the reserved RTS group range
    (1,000,000 - 2,097,151) so a single frame can drive every motor
    paired to the group.
    """
    if not isinstance(address, str) or not address.isdigit() or len(address) != 8:
        return False
    value = int(address)
    if group:
        return GROUP_ADDRESS_MIN <= value <= GROUP_ADDRESS_MAX
    # Individual devices must stay outside the reserved group range.
    return (
        DEVICE_ADDRESS_MIN <= value <= DEVICE_ADDRESS_MAX
        and not GROUP_ADDRESS_MIN <= value <= GROUP_ADDRESS_MAX
    )


def get_used_addresses(hass: HomeAssistant) -> set[int]:
    """Return all addresses used by existing config entries."""
    used: set[int] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        for device in entry.data.get("devices", []):
            try:
                used.add(int(device["address"]))
            except (KeyError, TypeError, ValueError):
                continue
        for sensor in entry.data.get("sensors", []):
            try:
                used.add(int(sensor["address"]))
            except (KeyError, TypeError, ValueError):
                continue
    return used


def is_address_used(hass: HomeAssistant, address: str) -> bool:
    """Return whether an address is already used by a device."""
    try:
        return int(address) in get_used_addresses(hass)
    except ValueError:
        return True


def generate_address(hass: HomeAssistant, group: bool = False) -> str:
    """Generate an unused random 8-digit address."""
    used = get_used_addresses(hass)
    low, high = (
        (GROUP_ADDRESS_MIN, GROUP_ADDRESS_MAX)
        if group
        else (DEVICE_ADDRESS_MIN, DEVICE_ADDRESS_MAX)
    )
    for _ in range(100):
        if group:
            address_int = random.randint(low, high)
        elif random.random() < (
            GROUP_ADDRESS_MIN / (DEVICE_ADDRESS_MAX + 1)
        ):
            # Below the reserved group range.
            address_int = random.randint(
                DEVICE_ADDRESS_MIN, GROUP_ADDRESS_MIN - 1
            )
        else:
            # Above the reserved group range.
            address_int = random.randint(
                GROUP_ADDRESS_MAX + 1, DEVICE_ADDRESS_MAX
            )
        if address_int not in used:
            return f"{address_int:08d}"
    raise RuntimeError("Failed to generate an unused RTS address")
