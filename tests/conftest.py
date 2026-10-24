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
    """The wizard holds long presses out on air: no real sleeping."""
    monkeypatch.setattr(rf, "press_air_time_s", lambda *args, **kwargs: 0.0)


@pytest.fixture
def fake_rf(monkeypatch):
    """Replace the rf helpers with a recorder."""
    fake = FakeRF()
    monkeypatch.setattr(rf, "async_send_command", fake.async_send_command)
    monkeypatch.setattr(rf, "get_transmitters", fake.get_transmitters)
    return fake


@pytest.fixture
def no_timer(monkeypatch):
    """Replace the position tracking timer with a manual recorder."""
    from custom_components.rts_gateway import cover

    cancels: list[bool] = []

    def fake_track(hass, action, interval):
        return lambda: cancels.append(True)

    monkeypatch.setattr(cover, "async_track_time_interval", fake_track)
    return cancels


def make_runtime_data(entities=None, sensors=None):
    """Runtime data stand-in matching entry.runtime_data usage."""
    from types import SimpleNamespace

    return SimpleNamespace(
        entities=entities if entities is not None else {},
        sensors=sensors if sensors is not None else {},
    )
