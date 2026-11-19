"""Shared fixtures for the RTS test suite."""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import pytest
from fakes import FakeHass, FakeHAStore, FakeRF

from custom_components.rts_gateway import rf, rolling_code


@pytest.fixture
def hass():
    return FakeHass()


@pytest.fixture(autouse=True)
def fake_store(monkeypatch):
    """Back RollingCodeStore with an in-memory store (all tests)."""
    FakeHAStore.reset()
    monkeypatch.setattr(rolling_code, "Store", FakeHAStore)
    return FakeHAStore


@pytest.fixture(autouse=True)
def no_press_hold(monkeypatch):
    """The wizard holds long presses out on air: no real sleeping.

    press_air_time_s=0 and TX_GAP_S=0 keep rf.async_send_command's
    inter-burst wait at zero for the tests that call it directly with a
    frozen time.monotonic (most tests use fake_rf, which replaces
    async_send_command entirely)."""
    monkeypatch.setattr(rf, "press_air_time_s", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(rf, "TX_GAP_S", 0.0)


@pytest.fixture(autouse=True)
def fake_issues(monkeypatch):
    """Back the issue registry helpers with recorders (all tests)."""
    from homeassistant.helpers import issue_registry as ir

    created: list = []
    deleted: list = []

    def _create(hass, domain, issue_id, **kwargs):
        created.append({"domain": domain, "issue_id": issue_id, **kwargs})

    def _delete(hass, domain, issue_id):
        deleted.append((domain, issue_id))

    monkeypatch.setattr(ir, "async_create_issue", _create)
    monkeypatch.setattr(ir, "async_delete_issue", _delete)
    from types import SimpleNamespace

    return SimpleNamespace(created=created, deleted=deleted)


@pytest.fixture(autouse=True)
def fake_entity_registry(monkeypatch):
    """Fake registry of the known entity ids."""
    from homeassistant.helpers import entity_registry as er

    entities: dict = {}

    class _Registry:
        def async_get(self, entity_id):
            return entities.get(entity_id)

    monkeypatch.setattr(er, "async_get", lambda hass: _Registry())
    return entities


@pytest.fixture(autouse=True)
def no_member_frame_gap(monkeypatch):
    """Group member commands are spaced on air: no real sleeping.

    Several tests freeze time.monotonic while a movement runs; a
    positive asyncio.sleep would then never wake up.
    """
    from custom_components.rts_gateway import cover

    monkeypatch.setattr(cover, "GROUP_MEMBER_FRAME_GAP_S", 0)


@pytest.fixture
def fake_rf(monkeypatch):
    """Replace the rf helpers with a recorder."""
    fake = FakeRF()
    monkeypatch.setattr(rf, "async_send_command", fake.async_send_command)
    monkeypatch.setattr(rf, "get_transmitters", fake.get_transmitters)
    return fake


@pytest.fixture
def no_timer(monkeypatch):
    """Replace the tracking timers with manual recorders."""
    from custom_components.rts_gateway import cover

    timers: list[dict] = []

    def fake_track(hass, action, interval):
        timer = {"action": action, "interval": interval, "cancelled": False}

        def cancel():
            timer["cancelled"] = True

        timers.append(timer)
        return cancel

    monkeypatch.setattr(cover, "async_track_time_interval", fake_track)
    return timers


@pytest.fixture(autouse=True)
def no_call_later(monkeypatch):
    """Record async_call_later timers instead of scheduling them.

    If cover.py ever schedules another async_call_later, tests must fire
    the recorded action manually (e.g. entity._async_stop_timer_fired(None))."""
    from custom_components.rts_gateway import cover

    timers: list[dict] = []

    def fake_call_later(hass, delay, action):
        timer = {"delay": delay, "action": action, "cancelled": False}

        def cancel():
            timer["cancelled"] = True

        timers.append(timer)
        return cancel

    monkeypatch.setattr(cover, "async_call_later", fake_call_later)
    return timers


def make_runtime_data(entities=None, sensors=None, my_sensors=None, my_numbers=None):
    """Runtime data stand-in matching entry.runtime_data usage."""
    from types import SimpleNamespace

    return SimpleNamespace(
        entities=entities if entities is not None else {},
        sensors=sensors if sensors is not None else {},
        my_sensors=my_sensors if my_sensors is not None else {},
        my_numbers=my_numbers if my_numbers is not None else {},
    )
