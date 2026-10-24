"""Test doubles for the Home Assistant parts of the RTS integration.

These fakes cover exactly the surface of ``HomeAssistant`` used by the
integration: ``hass.data``, the event bus, the service registry, the
config-entry manager and the entity restore state API.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

from custom_components.rts_gateway.const import (
    DEFAULT_BRIGHTNESS_STEP_PERCENT,
    DEFAULT_STEP_SIZE,
    DEFAULT_TILT_STEP_PERCENT,
    SomfyCommand,
    SomfyDeviceType,
    SomfyProtocol,
)


class FakeServiceRegistry:
    """Records service registrations like hass.services."""

    def __init__(self) -> None:
        self.registered: dict[tuple[str, str], tuple[Any, Any]] = {}

    def async_register(self, domain, service, handler, schema=None):
        self.registered[(domain, service)] = (handler, schema)

    def async_remove(self, domain, service):
        self.registered.pop((domain, service), None)


class FakeStates:
    """Minimal stand-in for hass.states."""

    def __init__(self) -> None:
        self._states: dict = {}

    def get(self, entity_id):
        return self._states.get(entity_id)

    def set(self, entity_id, state):
        self._states[entity_id] = state


class FakeBus:
    """Records event subscriptions and fires like hass.bus."""

    def __init__(self) -> None:
        self.listeners: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []

    def async_listen(self, event_type, handler):
        self.listeners[event_type] = handler

        def unsubscribe():
            self.listeners.pop(event_type, None)

        return unsubscribe

    def async_fire(self, event_type, data=None, context=None):
        self.events.append({"type": event_type, "data": data or {}})


class FakeConfigEntries:
    """Minimal stand-in for hass.config_entries."""

    def __init__(self) -> None:
        self.entries: list[FakeConfigEntry] = []
        self.updated: list[tuple[FakeConfigEntry, dict]] = []
        self.forwarded: list[tuple[FakeConfigEntry, list]] = []
        self.unloaded: list[tuple[FakeConfigEntry, list]] = []
        self.reloads: list[str] = []

    def async_entries(self, domain, include_ignore=False):
        return [e for e in self.entries if e.domain == domain]

    def async_get_known_entry(self, entry_id):
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    def async_update_entry(self, entry, **kwargs):
        self.updated.append((entry, kwargs))
        if "data" in kwargs:
            entry.data = kwargs["data"]
        if "options" in kwargs:
            entry.options = kwargs["options"]

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append((entry, list(platforms)))

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append((entry, list(platforms)))
        return True

    async def async_reload(self, entry_id):
        self.reloads.append(entry_id)


class FakeDeviceEntry:
    """Minimal DeviceEntry stand-in."""

    def __init__(self, id, config_entry_id, identifiers=()):
        self.id = id
        self.config_entry_id = config_entry_id
        self.identifiers = set(identifiers)


class FakeDeviceItems:
    """Items view matching what async_entries_for_config_entry uses."""

    def __init__(self) -> None:
        self._items: dict[str, FakeDeviceEntry] = {}

    def get_devices_for_config_entry_id(self, config_entry_id):
        return [
            d for d in self._items.values()
            if d.config_entry_id == config_entry_id
        ]


class FakeDeviceRegistry:
    """Records devices and removals like the device registry."""

    def __init__(self) -> None:
        self._devices = FakeDeviceItems()
        self.removed: list[str] = []

    def async_get_or_create(self, config_entry_id, identifiers):
        device = FakeDeviceEntry(
            id=f"dev{len(self._devices._items)}",
            config_entry_id=config_entry_id,
            identifiers=identifiers,
        )
        self._devices._items[device.id] = device
        return device

    def async_remove_device(self, device_id):
        self.removed.append(device_id)
        self._devices._items.pop(device_id, None)


class FakeHass:
    """Minimal HomeAssistant stand-in."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.bus = FakeBus()
        self.services = FakeServiceRegistry()
        self.config_entries = FakeConfigEntries()
        self.states = FakeStates()
        self.data["device_registry"] = FakeDeviceRegistry()

    def async_create_task(self, target):
        return asyncio.get_running_loop().create_task(target)


class FakeConfigEntry:
    """Minimal ConfigEntry stand-in."""

    def __init__(self, entry_id="test_entry", domain="rts_gateway", data=None, options=None, runtime_data=None):
        self.entry_id = entry_id
        self.runtime_data = runtime_data
        self.domain = domain
        self.data: dict[str, Any] = dict(data or {})
        self.options: dict[str, Any] = dict(options or {})
        self.update_listeners: list[Any] = []

    def add_update_listener(self, listener):
        self.update_listeners.append(listener)

        def remove():
            self.update_listeners.remove(listener)

        return remove

    def async_on_unload(self, update_callback):
        return update_callback


class FakeHAStore:
    """In-memory replacement for homeassistant.helpers.storage.Store."""

    _bank: ClassVar[dict[str, dict]] = {}

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        data = self._bank.get(self.key)
        return dict(data) if isinstance(data, dict) else None

    async def async_save(self, data):
        FakeHAStore._bank[self.key] = dict(data)

    @classmethod
    def reset(cls):
        cls._bank = {}


class FakeRF:
    """Stands in for the rf helpers: records sent commands."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.transmitters = ["radio_frequency.test_radio"]

    def get_transmitters(self, hass):
        return list(self.transmitters)

    async def async_send_command(
        self,
        hass,
        transmitter_id,
        address,
        command,
        rolling_code,
        protocol=SomfyProtocol.RTS_56BIT,
        frame_repeats=3,
        step_size=DEFAULT_STEP_SIZE,
    ):
        self.sent.append(
            {
                "transmitter_id": transmitter_id,
                "address": address,
                "command": command.value,
                "rolling_code": rolling_code,
                "protocol": str(protocol),
                "repeats": frame_repeats,
                "step_size": step_size,
            }
        )


def make_device(**overrides) -> dict[str, Any]:
    """Return a device config dict as stored in a config entry."""
    device: dict[str, Any] = {
        "name": "Test device",
        "address": "12345678",
        "device_type": SomfyDeviceType.ROLLER_SHUTTER.value,
        "transmitter_id": "radio_frequency.test_radio",
        "protocol": SomfyProtocol.RTS_56BIT.value,
        "open_time_ms": 1000,
        "close_time_ms": 1000,
        "is_group": False,
        "invert": False,
        "invert_position": False,
        "tilt": False,
        "tilt_step_percent": DEFAULT_TILT_STEP_PERCENT,
        "step_size": DEFAULT_STEP_SIZE,
        "toggle_command": SomfyCommand.MY.value,
        "brightness_step_percent": DEFAULT_BRIGHTNESS_STEP_PERCENT,
        "linked_remote_addresses": [],
    }
    device.update(overrides)
    return device


def make_sensor_config(address="11112222", name="Test sensor") -> dict[str, Any]:
    return {
        "name": name,
        "address": address,
        "transmitter_id": "radio_frequency.test_radio",
    }


def prepare_entity(entity, entity_id):
    """Wire an entity for standalone tests (no real hass machinery)."""
    entity.entity_id = entity_id
    entity.async_write_ha_state = MagicMock()
    entity.async_get_last_state = AsyncMock(return_value=None)
    return entity
