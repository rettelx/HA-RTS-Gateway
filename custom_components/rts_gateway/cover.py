"""Cover platform for the RTS integration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from . import rf
from .availability import TransmitterAvailabilityMixin
from .const import (
    CONF_ADDRESS,
    CONF_CLOSE_TIME_MS,
    CONF_DEVICE_TYPE,
    CONF_GROUP,
    CONF_GROUP_ADDRESSES,
    CONF_INVERT,
    CONF_INVERT_POSITION,
    CONF_LINKED_REMOTE_ADDRESSES,
    CONF_MY_POSITION,
    CONF_NAME,
    CONF_OPEN_TIME_MS,
    CONF_PROTOCOL,
    CONF_SIMULATE_MY,
    CONF_STEP_SIZE,
    CONF_STOP_METHOD,
    CONF_TILT,
    CONF_TILT_STEP_PERCENT,
    CONF_TILT_TIME_MS,
    CONF_TRANSMITTER_ID,
    COVER_DEVICE_TYPES,
    DEFAULT_CLOSE_TIME_MS,
    DEFAULT_FRAME_REPEATS,
    DEFAULT_OPEN_TIME_MS,
    DEFAULT_STEP_SIZE,
    DEFAULT_TILT_STEP_PERCENT,
    DEFAULT_TILT_TIME_MS,
    DEVICE_TYPE_MAPPING,
    DEVICE_TYPE_TO_DEVICE_CLASS,
    DOMAIN,
    SENSOR_SUN_BIT,
    SENSOR_WIND_BIT,
    SETMY_REPEATS,
    STOP_METHOD_PULSE,
    TILT_PRESS_END_S,
    TILT_PRESS_REPEATS,
    TILT_STEP_INTERVAL_S,
    SomfyCommand,
    SomfyDeviceType,
    SomfyProtocol,
    device_type_label,
)
from .rolling_code import RollingCodeStore

# Radio actions are serialized per entity to avoid overlapping bursts.
PARALLEL_UPDATES = 1
_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=1)
# Update cadence of the timed tilt estimate (56-bit tilt motors)
_TILT_TIMED_TICK_S = 0.25
# Poll cadence while waiting for a remote press to end
_PRESS_POLL_S = 0.1

# Command swap applied when the motor is mounted upside down: HA "open"
# must drive the motor's DOWN, and vice versa, on both TX and RX.
_INVERT_COMMANDS = {
    SomfyCommand.UP: SomfyCommand.DOWN,
    SomfyCommand.DOWN: SomfyCommand.UP,
    SomfyCommand.STEP_UP: SomfyCommand.STEP_DOWN,
    SomfyCommand.STEP_DOWN: SomfyCommand.STEP_UP,
}


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return min(high, max(low, value))


class PositionEstimator:
    """Estimates the cover position from movement time."""

    def __init__(self, open_time_ms: int, close_time_ms: int) -> None:
        """Initialize the estimator."""
        self._open_time_ms = max(open_time_ms, 100)
        self._close_time_ms = max(close_time_ms, 100)
        self._position = 100
        self._start_position = 100
        self._direction: str | None = None
        self._target: int | None = None
        self._started_at: float | None = None

    @property
    def position(self) -> int:
        """Current estimated position (0-100)."""
        if not self.is_moving:
            return self._position
        elapsed_ms = (time.monotonic() - (self._started_at or 0.0)) * 1000
        if self._direction == "up":
            position = self._start_position + elapsed_ms / self._open_time_ms * 100
        else:
            position = self._start_position - elapsed_ms / self._close_time_ms * 100
        return int(_clamp(int(position)))

    @property
    def is_moving(self) -> bool:
        """Return whether a movement is being tracked."""
        return self._direction is not None

    @property
    def direction(self) -> str | None:
        """Return the current movement direction, if any."""
        return self._direction

    @property
    def target(self) -> int | None:
        """Return the position target, if any."""
        return self._target

    def reached_target(self) -> bool:
        """Return whether the movement has reached its target or end stop."""
        if not self.is_moving:
            return True
        position = self.position
        if self._direction == "up" and position >= (self._target or 100):
            return True
        return bool(
            self._direction == "down" and position <= (self._target or 0)
        )

    def restore(self, position: int) -> None:
        """Restore a previously persisted position."""
        self._position = _clamp(position)
        self._start_position = self._position
        self._direction = None
        self._target = None
        self._started_at = None

    def start(self, direction: str, target: int | None = None) -> None:
        """Start tracking a movement."""
        self._position = self.position
        self._start_position = self._position
        self._direction = direction
        self._target = target
        self._started_at = time.monotonic()

    def stop(self) -> None:
        """Stop tracking movement at the current estimated position."""
        self._position = self.position
        self._start_position = self._position
        self._direction = None
        self._target = None
        self._started_at = None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RTS covers from a config entry."""
    devices = entry.data.get("devices", [])
    entities = [
        SomfyRTSCover(hass, entry, device)
        for device in devices
        if SomfyDeviceType(device.get(CONF_DEVICE_TYPE)) in COVER_DEVICE_TYPES
    ]
    async_add_entities(entities)


class SomfyRTSCover(TransmitterAvailabilityMixin, CoverEntity, RestoreEntity):
    """Representation of a RTS cover."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device_config: dict[str, Any],
    ) -> None:
        """Initialize the cover."""
        self._hass = hass
        self._entry = entry
        self._device_config = device_config
        self._store: RollingCodeStore = hass.data[DOMAIN]["rolling_codes"]

        self._address = str(device_config[CONF_ADDRESS])
        self._address_int = int(self._address)
        self._device_type = SomfyDeviceType(device_config[CONF_DEVICE_TYPE])
        self._transmitter_id = device_config[CONF_TRANSMITTER_ID]
        self._protocol = SomfyProtocol(
            device_config.get(CONF_PROTOCOL, SomfyProtocol.RTS_56BIT)
        )
        self._is_group = bool(device_config.get(CONF_GROUP, False))
        self._invert = bool(device_config.get(CONF_INVERT, False))
        self._invert_position = bool(
            device_config.get(CONF_INVERT_POSITION, False)
        )
        my_position = device_config.get(CONF_MY_POSITION)
        self._my_position = (
            _clamp(int(my_position)) if my_position is not None else None
        )
        self._linked_remote_addresses = [
            str(a) for a in device_config.get(CONF_LINKED_REMOTE_ADDRESSES, [])
        ]
        # RTS groups this cover is paired to (group addresses).
        self._group_addresses = [
            str(a) for a in device_config.get(CONF_GROUP_ADDRESSES, [])
        ]

        self._attr_unique_id = f"{DOMAIN}_{self._address}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=device_config.get(CONF_NAME, f"RTS {self._address}"),
            model=device_type_label(self._hass, self._device_type),
        )
        self._attr_device_class = DEVICE_TYPE_TO_DEVICE_CLASS.get(self._device_type)

        self._position_estimator = PositionEstimator(
            open_time_ms=device_config.get(CONF_OPEN_TIME_MS, DEFAULT_OPEN_TIME_MS),
            close_time_ms=device_config.get(CONF_CLOSE_TIME_MS, DEFAULT_CLOSE_TIME_MS),
        )

        # Tilt uses step frames on 80-bit devices; 56-bit drives a
        # separate tilt motor with timed Up/Down commands. The wizard
        # only offers the option for slat devices, but existing entries
        # keep working regardless of the device type.
        self._tilt_supported = bool(device_config.get(CONF_TILT, False))
        self._tilt_timed = self._protocol != SomfyProtocol.RTS_80BIT
        self._tilt_time_ms = int(
            device_config.get(CONF_TILT_TIME_MS, DEFAULT_TILT_TIME_MS)
        )
        self._tilt_step_percent = int(
            device_config.get(CONF_TILT_STEP_PERCENT, DEFAULT_TILT_STEP_PERCENT)
        )
        self._step_size = int(device_config.get(CONF_STEP_SIZE, DEFAULT_STEP_SIZE))
        self._tilt = 0
        self._tilt_task: asyncio.Task[None] | None = None
        # Some motors ignore the MY button: they are stopped with a
        # brief press of the opposite direction instead.
        self._stop_method = str(
            device_config.get(CONF_STOP_METHOD, "my")
        )
        # Native motors go to their own memorized favorite on MY;
        # otherwise the favorite position is simulated by HA.
        self._simulate_my = bool(device_config.get(CONF_SIMULATE_MY, True))
        self._recal_task: asyncio.Task[None] | None = None
        # True while this member mirrors a group command: the motor
        # already stopped with the group, never send radio for it.
        self._shadow_tracking = False
        self._press: dict[str, Any] | None = None
        self._press_task: asyncio.Task[None] | None = None
        self._tilt_track: dict[str, Any] | None = None
        self._tilt_track_task: asyncio.Task[None] | None = None

        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        if self._tilt_supported:
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.STOP_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
        self._attr_supported_features = features

        self._last_command: SomfyCommand | None = None
        self._last_rx_rolling_code: int | None = None
        self._last_rx_device: str | None = None
        self._last_rx_step: int | None = None
        self._wind: bool | None = None
        self._sun: bool | None = None
        self._cancel_timer: Callable[[], None] | None = None

    @property
    def icon(self) -> str | None:
        """Return the icon for the device type."""
        return DEVICE_TYPE_MAPPING.get(self._device_type, {}).get("icon")

    @property
    def name(self) -> str | None:
        """Return the entity name (device carries the friendly name)."""
        return None

    @property
    def current_cover_position(self) -> int:
        """Return the current estimated position (HA convention: 100 open).

        A group reports the average of its members: moving one member
        on its own shifts the group position accordingly.
        With invert_position, the reported scale is flipped: the fully
        open cover reads 0 and the fully closed cover reads 100.
        """
        average = self._group_member_average()
        if average is not None:
            return 100 - average if self._invert_position else average
        if self._invert_position:
            return 100 - self._position_estimator.position
        return self._position_estimator.position

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return the current estimated tilt position."""
        if not self._tilt_supported:
            return None
        return self._tilt

    @property
    def is_closed(self) -> bool:
        """Return whether the cover is closed."""
        if self._is_group:
            members = self._group_members()
            if members:
                # A group is closed only when every member is.
                return all(member.is_closed for member in members)
        return self._position_estimator.position == 0

    @property
    def is_closing(self) -> bool | None:
        """Return whether the cover is closing."""
        if not self._position_estimator.is_moving:
            return None
        return self._position_estimator.direction == "down"

    @property
    def is_opening(self) -> bool | None:
        """Return whether the cover is opening."""
        if not self._position_estimator.is_moving:
            return None
        return self._position_estimator.direction == "up"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes."""
        return {
            "address": self._address,
            "device_type": self._device_type.value,
            "protocol": self._protocol.value,
            "transmitter_id": self._transmitter_id,
            "is_group": self._is_group,
            "group_addresses": self._group_addresses,
            "members": self._group_member_ids(),
            "invert": self._invert,
            "invert_position": self._invert_position,
            "my_position": self._my_position,
            "last_command": self._last_command.value if self._last_command else None,
            "linked_remote_addresses": self._linked_remote_addresses,
            "last_rx_rolling_code": self._last_rx_rolling_code,
            "last_rx_device": self._last_rx_device,
            "last_rx_step": self._last_rx_step,
            "wind_alarm": self._wind,
            "sun_alarm": self._sun,
        }

    async def async_added_to_hass(self) -> None:
        """Register the entity and restore its last known position."""
        await super().async_added_to_hass()

        registry = self._entry.runtime_data.entities
        registry[self.entity_id] = self

        last_state = await self.async_get_last_state()
        if last_state is not None:
            position = last_state.attributes.get("current_position")
            if isinstance(position, (int, float)):
                stored = int(position)
                if self._invert_position:
                    stored = 100 - stored  # stored value is on the flipped scale
                self._position_estimator.restore(stored)
            tilt = last_state.attributes.get("current_tilt_position")
            if self._tilt_supported and isinstance(tilt, (int, float)):
                self._tilt = _clamp(int(tilt))

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the entity."""
        registry = self._entry.runtime_data.entities
        registry.pop(self.entity_id, None)
        self._cancel_tilt_task()
        self._cancel_tilt_tracking()
        self._cancel_press()
        if self._recal_task is not None and not self._recal_task.done():
            self._recal_task.cancel()
        self._recal_task = None
        self._stop_tracking()
        await super().async_will_remove_from_hass()

    async def _async_send_command(
        self, command: SomfyCommand, frame_repeats: int = DEFAULT_FRAME_REPEATS
    ) -> None:
        """Send a logical command through the RF transmitter."""
        if self._invert:
            command = _INVERT_COMMANDS.get(command, command)
        await self._async_transmit(command, frame_repeats)
        self._last_command = command

    async def async_send_raw_command(
        self,
        command: SomfyCommand,
        frame_repeats: int = DEFAULT_FRAME_REPEATS,
        step_size: int | None = None,
    ) -> None:
        """Send a raw radio command (bypasses the invert mapping)."""
        await self._async_transmit(command, frame_repeats, step_size)

    async def _async_transmit(
        self,
        command: SomfyCommand,
        frame_repeats: int,
        step_size: int | None = None,
    ) -> None:
        """Transmit a radio command and register it for loopback filtering."""
        rolling_code = await self._store.async_get_next(self._address_int)
        await rf.async_send_command(
            self._hass,
            self._transmitter_id,
            self._address_int,
            command,
            rolling_code,
            protocol=self._protocol,
            frame_repeats=frame_repeats,
            step_size=step_size if step_size is not None else self._step_size,
        )
        # Remember what we sent so the RX handler can ignore the loopback
        # (the gateway's own receiver hears our transmissions).
        recent_tx = self._hass.data[DOMAIN].setdefault("recent_tx", {})
        recent_tx[(self._address_int, rolling_code)] = time.monotonic()
        _LOGGER.debug(
            "Sent %s to %s (protocol %s, rolling code %d)",
            command.value,
            self._address,
            self._protocol.value,
            rolling_code,
        )

    # =====================================================================
    # Position (time-based estimate)
    # =====================================================================
    def _start_tracking(self) -> None:
        """Start periodic position updates while moving."""
        if self._cancel_timer is not None:
            return
        self._cancel_timer = async_track_time_interval(
            self._hass, self._async_movement_tick, UPDATE_INTERVAL
        )

    def _stop_tracking(self) -> None:
        """Stop periodic position updates."""
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    async def _async_stop_at_target(self) -> None:
        """Stop the motor at an intermediate position."""
        if (
            self._stop_method == STOP_METHOD_PULSE
            and self._position_estimator.direction == "down"
        ):
            # Motors without MY support: a brief press of the opposite
            # direction stops the movement (standard RTS behavior).
            await self._async_send_command(SomfyCommand.UP)
        elif self._stop_method == STOP_METHOD_PULSE:
            await self._async_send_command(SomfyCommand.DOWN)
        else:
            await self._async_send_command(SomfyCommand.STOP)

    async def _async_movement_tick(self, now: Any) -> None:
        """Update the estimated position, stopping the movement when done."""
        if not self._position_estimator.is_moving:
            self._stop_tracking()
            return
        if self._shadow_tracking and self._position_estimator.reached_target():
            # Mirrored from a group command: the motor stopped with the
            # group, no radio from this member.
            self._position_estimator.stop()
            self._shadow_tracking = False
            self._stop_tracking()
        elif self._position_estimator.reached_target():
            target = self._position_estimator.target
            if target not in (None, 0, 100):
                # Intermediate target: the motor must be stopped explicitly.
                try:
                    await self._async_stop_at_target()
                except HomeAssistantError as err:
                    _LOGGER.warning("Failed to stop %s: %s", self.entity_id, err)
            self._position_estimator.stop()
            self._stop_tracking()
            if self._is_group:
                self._stop_members_tracking()
        self.async_write_ha_state()
        self._notify_parent_groups()

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self._async_send_command(SomfyCommand.UP)
        self._position_estimator.start("up", 100)
        self._start_tracking()
        if self._is_group:
            self._track_members_movement("up", 100)
        self.async_write_ha_state()
        self._notify_parent_groups()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self._async_send_command(SomfyCommand.DOWN)
        self._position_estimator.start("down", 0)
        self._start_tracking()
        if self._is_group:
            self._track_members_movement("down", 0)
        self.async_write_ha_state()
        self._notify_parent_groups()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        if self._position_estimator.is_moving:
            await self._async_stop_at_target()
        else:
            await self._async_send_command(SomfyCommand.STOP)
        self._position_estimator.stop()
        self._stop_tracking()
        if self._is_group:
            self._stop_members_tracking()
        self.async_write_ha_state()
        self._notify_parent_groups()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        target = int(kwargs["position"])
        if self._invert_position:
            target = 100 - target  # the user thinks in "% closed"
        current = self._position_estimator.position
        average = self._group_member_average()
        if average is not None:
            # A group moves from what it displays: the member average.
            current = 100 - average if self._invert_position else average
        if target == current:
            return
        direction = "up" if target > current else "down"
        await self._async_send_command(
            SomfyCommand.UP if direction == "up" else SomfyCommand.DOWN
        )
        self._position_estimator.start(direction, target)
        self._start_tracking()
        if self._is_group:
            self._track_members_movement(direction, target)
        self.async_write_ha_state()
        self._notify_parent_groups()

    # =====================================================================
    # Tilt (80-bit step wheel)
    # =====================================================================
    @property
    def linked_remote_addresses_int(self) -> list[int]:
        """Return linked remote addresses as integers."""
        return [int(a) for a in self._linked_remote_addresses]

    def async_update_linked_remotes(self, addresses: list[str]) -> None:
        """Update the linked remote addresses."""
        self._linked_remote_addresses = [str(a) for a in addresses]
        self._device_config[CONF_LINKED_REMOTE_ADDRESSES] = self._linked_remote_addresses
        self.async_write_ha_state()

    def async_update_group_addresses(self, addresses: list[str]) -> None:
        """Update the RTS group addresses this cover belongs to."""
        self._group_addresses = [str(a) for a in addresses]
        self._device_config[CONF_GROUP_ADDRESSES] = self._group_addresses
        self.async_write_ha_state()

    def _group_member_ids(self) -> list[str]:
        """Return the entity ids paired to this group address."""
        if not self._is_group:
            return []
        members: list[str] = []
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if runtime is None:
                continue
            for entity_id, entity in runtime.entities.items():
                if entity is self:
                    continue
                if self._address in getattr(entity, "_group_addresses", []):
                    members.append(str(entity_id))
        return sorted(members)

    # =====================================================================
    # Group propagation: the member motors hear the group frames, their
    # estimates must follow (mirror mode, no radio from the members).
    # =====================================================================
    def _group_members(self) -> list[SomfyRTSCover]:
        """Live member entities paired to this group's address."""
        if not self._is_group:
            return []
        members: list[SomfyRTSCover] = []
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if runtime is None:
                continue
            for entity in runtime.entities.values():
                if entity is self or not isinstance(entity, SomfyRTSCover):
                    continue
                if self._address in entity._group_addresses:
                    members.append(entity)
        return members

    def _group_member_average(self) -> int | None:
        """Average position of the live members (display scale)."""
        if not self._is_group:
            return None
        members = self._group_members()
        if not members:
            return None
        return round(
            sum(member.current_cover_position for member in members) / len(members)
        )

    def _parent_groups(self) -> list[SomfyRTSCover]:
        """Live group entities this cover is a member of."""
        if not self._group_addresses:
            return []
        groups: list[SomfyRTSCover] = []
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if runtime is None:
                continue
            for entity in runtime.entities.values():
                if entity is self or not isinstance(entity, SomfyRTSCover):
                    continue
                if entity._is_group and entity._address in self._group_addresses:
                    groups.append(entity)
        return groups

    def _notify_parent_groups(self) -> None:
        """Refresh the parent group states after this member moved."""
        for group in self._parent_groups():
            group.async_write_ha_state()

    def _track_members_movement(self, direction: str, target: int) -> None:
        """Move the member estimates with the group (logical target)."""
        display = 100 - target if self._invert_position else target
        for member in self._group_members():
            member.async_track_group_movement(direction, display)

    def _stop_members_tracking(self) -> None:
        """Freeze the member estimates when the group stops."""
        for member in self._group_members():
            member.async_stop_group_tracking()

    def _restore_members_position(self, position: int) -> None:
        """Anchor the member estimates (display scale)."""
        for member in self._group_members():
            member.async_track_group_restore(position)

    def _track_members_native_my(self) -> None:
        """Members head to their own MY position on a native group MY."""
        for member in self._group_members():
            member.async_track_group_native_my()

    def _track_members_tilt_step(self, up: bool) -> None:
        """Apply one tilt step to the member estimates."""
        for member in self._group_members():
            member._apply_rx_step(up)

    def _track_members_tilt_snap(self, target: int) -> None:
        """Snap the member tilt estimates after a group tilt movement."""
        for member in self._group_members():
            member.async_track_group_tilt_snap(target)

    def async_track_group_movement(self, direction: str, target: int) -> None:
        """Mirror a group movement on this member (no radio)."""
        display = _clamp(int(target))
        logical = 100 - display if self._invert_position else display
        self._position_estimator.start(direction, logical)
        self._shadow_tracking = True
        self._start_tracking()
        self.async_write_ha_state()

    def async_stop_group_tracking(self) -> None:
        """Freeze the mirrored group movement on this member."""
        self._shadow_tracking = False
        self._position_estimator.stop()
        self._stop_tracking()
        self.async_write_ha_state()

    def async_track_group_restore(self, position: int) -> None:
        """Anchor the member estimate after a group cycle (display scale)."""
        display = _clamp(int(position))
        logical = 100 - display if self._invert_position else display
        self._shadow_tracking = False
        self._position_estimator.restore(logical)
        self._stop_tracking()
        self.async_write_ha_state()

    def async_track_group_native_my(self) -> None:
        """Mirror a native group MY press toward this member's own MY."""
        if self._my_position is None:
            return
        if self._my_position == self.current_cover_position:
            return
        logical = (
            100 - self._my_position if self._invert_position else self._my_position
        )
        current = self._position_estimator.position
        self._position_estimator.start(
            "up" if logical > current else "down", logical
        )
        self._shadow_tracking = True
        self._start_tracking()
        self.async_write_ha_state()

    def async_track_group_tilt_snap(self, target: int) -> None:
        """Snap the member tilt estimate after a group tilt movement."""
        self._cancel_tilt_task()
        self._cancel_tilt_tracking()
        self._tilt = _clamp(int(target))
        self.async_write_ha_state()

    def _cancel_tilt_task(self) -> None:
        """Cancel a running tilt movement."""
        if self._tilt_task is not None and not self._tilt_task.done():
            self._tilt_task.cancel()
        self._tilt_task = None

    async def _async_tilt_to_target(self, target: int) -> None:
        """Move the tilt to the target (protocol-dependent mechanism)."""
        if self._tilt_timed:
            await self._async_tilt_to_target_timed(target)
            return
        await self._async_tilt_to_target_step(target)

    async def _async_tilt_to_target_timed(self, target: int) -> None:
        """Drive a 56-bit tilt motor with timed Up/Down commands.

        The tilt motor runs while the button is held; the travel time
        (tilt_time_ms, full sweep) sets how long to wait before
        stopping with MY.
        """
        delta = target - self._tilt
        if delta == 0:
            return
        await self._async_send_command(
            SomfyCommand.UP if delta > 0 else SomfyCommand.DOWN,
            frame_repeats=TILT_PRESS_REPEATS,
        )
        start = self._tilt
        duration = abs(delta) / 100.0 * self._tilt_time_ms / 1000.0
        elapsed = 0.0
        while elapsed < duration:
            tick = min(_TILT_TIMED_TICK_S, duration - elapsed)
            await asyncio.sleep(tick)
            elapsed += tick
            self._tilt = _clamp(round(start + delta * (elapsed / duration)))
            self.async_write_ha_state()
        self._tilt = _clamp(target)
        self.async_write_ha_state()
        # The tilt motor has no position target of its own: stop it.
        try:
            await self._async_send_command(SomfyCommand.STOP)
        except HomeAssistantError as err:
            _LOGGER.warning("Failed to stop tilt on %s: %s", self.entity_id, err)

    async def _async_tilt_to_target_step(self, target: int) -> None:
        """Send step commands until the tilt target is reached."""
        delta = target - self._tilt
        if delta == 0:
            return
        command = SomfyCommand.STEP_UP if delta > 0 else SomfyCommand.STEP_DOWN
        step = self._tilt_step_percent
        count = max(1, round(abs(delta) / step))
        # Cancellation (stop) keeps the last partial estimate: the snap
        # below only runs once every step frame has been sent.
        for _ in range(count):
            await self._async_send_command(command)
            self._tilt = _clamp(self._tilt + (step if delta > 0 else -step))
            if self._is_group:
                self._track_members_tilt_step(delta > 0)
            self.async_write_ha_state()
            await asyncio.sleep(TILT_STEP_INTERVAL_S)
        # Snap to the exact target on completion
        self._tilt = _clamp(target)
        if self._is_group:
            self._track_members_tilt_snap(target)
        self.async_write_ha_state()

    def _async_start_tilt(self, target: int) -> None:
        """Start (or restart) a tilt movement toward the target."""
        self._cancel_tilt_task()
        self._cancel_tilt_tracking()
        self._tilt_task = self._hass.async_create_task(
            self._async_tilt_to_target(target)
        )
        if self._is_group and self._tilt_timed:
            # Timed tilt motors follow the group frames: mirror the
            # interpolation on the members (their own tilt time).
            for member in self._group_members():
                member._start_tilt_tracking(_clamp(target))

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Tilt the cover fully open."""
        self._async_start_tilt(100)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Tilt the cover fully closed."""
        self._async_start_tilt(0)

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Stop an ongoing tilt movement."""
        timed_active = self._tilt_timed and (
            (self._tilt_task is not None and not self._tilt_task.done())
            or self._tilt_track_task is not None
        )
        self._cancel_tilt_task()
        self._cancel_tilt_tracking()
        self._cancel_press()
        if self._is_group:
            for member in self._group_members():
                member._cancel_tilt_task()
                member._cancel_tilt_tracking()
                member._cancel_press()
        if timed_active:
            # A timed tilt motor keeps running until it hears MY.
            try:
                await self._async_send_command(SomfyCommand.STOP)
            except HomeAssistantError as err:
                _LOGGER.warning("Failed to stop tilt on %s: %s", self.entity_id, err)
        self.async_write_ha_state()

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Tilt the cover to a specific position."""
        self._async_start_tilt(_clamp(int(kwargs["tilt_position"])))

    async def async_goto_my_position(self) -> None:
        """Move the cover to its configured My (favorite) position."""
        if not self._simulate_my:
            # Native mode: the motor goes to its own memorized
            # favorite position when it hears MY.
            await self._async_send_command(SomfyCommand.MY)
            self._native_my_tracking()
            self.async_write_ha_state()
            self._notify_parent_groups()
            return
        await self.async_set_cover_position(position=self._my_position)

    def async_set_my_position(self, position: int) -> None:
        """Store a new My (favorite) position (display scale)."""
        self._my_position = _clamp(int(position))
        self._device_config[CONF_MY_POSITION] = self._my_position
        self.async_write_ha_state()

    def async_set_current_position(self, position: int) -> None:
        """Realign the position estimate without moving the motor."""
        display = _clamp(int(position))
        logical = 100 - display if self._invert_position else display
        self._position_estimator.restore(logical)
        self._stop_tracking()
        if self._is_group:
            self._restore_members_position(display)
        self.async_write_ha_state()
        self._notify_parent_groups()

    def async_set_current_tilt_position(self, tilt: int) -> None:
        """Realign the tilt estimate without moving the motor."""
        self._cancel_tilt_task()
        self._cancel_tilt_tracking()
        self._tilt = _clamp(int(tilt))
        if self._is_group:
            self._track_members_tilt_snap(_clamp(int(tilt)))
        self.async_write_ha_state()

    def _native_my_tracking(self) -> None:
        """Track the estimate toward the native favorite position."""
        if self._my_position is None:
            return
        target = self._my_position  # display scale
        if target == self.current_cover_position:
            return
        logical = 100 - target if self._invert_position else target
        current = self._position_estimator.position
        self._position_estimator.start(
            "up" if logical > current else "down", logical
        )
        self._start_tracking()
        if self._is_group:
            self._track_members_native_my()

    async def async_memorize_my_position(self) -> None:
        """Send the long MY press that memorizes the current position.

        Only meaningful for native motors (simulate_my disabled): the
        motor stores its current physical position as the favorite.
        """
        await self._async_send_command(
            SomfyCommand.MY, frame_repeats=SETMY_REPEATS
        )

    def async_recalibrate(self) -> None:
        """Start a cycle to the nearest end stop to re-anchor the estimate."""
        if self._recal_task is not None and not self._recal_task.done():
            return
        self._recal_task = self._hass.async_create_task(
            self._async_recalibrate_cycle()
        )

    async def _async_recalibrate_cycle(self) -> None:
        """Travel to the nearest end stop, then back to the start.

        One anchor is enough: from a verified end, extrapolation with
        the configured travel times is exact, and full open/close
        commands issued afterwards re-verify their own end stop on
        their own (no stop command is sent for the end targets, the
        motor cuts at the stop). Going to the nearest end halves the
        cycle. The phase waits 1.3x the configured travel time, so a
        slightly under-measured duration still lets the motor reach
        its stop before the return move.
        """
        start_position = self.current_cover_position
        if start_position <= 50:
            travel_ms = int(
                self._device_config.get(
                    CONF_CLOSE_TIME_MS, DEFAULT_CLOSE_TIME_MS
                )
            )
            await self.async_close_cover()
        else:
            travel_ms = int(
                self._device_config.get(CONF_OPEN_TIME_MS, DEFAULT_OPEN_TIME_MS)
            )
            await self.async_open_cover()
        await asyncio.sleep(travel_ms / 1000 * 1.3 + 1)
        if self._is_group:
            self._stop_members_tracking()
        # Back to where the cover was before the recalibration.
        if start_position != self.current_cover_position:
            await self.async_set_cover_position(position=start_position)
        self._recal_task = None

    def _apply_rx_step(self, up: bool) -> None:
        """Apply a step command received over the air to the tilt estimate."""
        if not self._tilt_supported:
            return
        self._tilt = _clamp(self._tilt + (self._tilt_step_percent if up else -self._tilt_step_percent))
        self.async_write_ha_state()

    def _cancel_press(self) -> None:
        """Drop a pending remote press resolution."""
        if self._press_task is not None and not self._press_task.done():
            self._press_task.cancel()
        self._press_task = None
        self._press = None

    def _track_remote_press(
        self, command: SomfyCommand, rolling_code: int, repeat: int
    ) -> None:
        """Accumulate the repeats of an Up/Down press from a remote."""
        now = time.monotonic()
        if (
            self._press is not None
            and self._press["command"] == command
            and self._press["code"] == rolling_code
        ):
            self._press["count"] = repeat
            self._press["last_seen"] = now
            return
        # A new press replaces whatever was pending.
        self._cancel_press()
        self._cancel_tilt_tracking()
        self._press = {
            "command": command,
            "code": rolling_code,
            "count": repeat,
            "last_seen": now,
        }
        self._press_task = self._hass.async_create_task(
            self._async_resolve_press()
        )

    async def _async_resolve_press(self) -> None:
        """Decide lift vs tilt once a remote press has ended."""
        press = self._press
        assert press is not None
        while time.monotonic() - press["last_seen"] < TILT_PRESS_END_S:
            await asyncio.sleep(_PRESS_POLL_S)
        self._press = None
        self._press_task = None
        if press["count"] >= TILT_PRESS_REPEATS:
            # Long press: the tilt motor runs until its end stop.
            self._start_tilt_tracking(
                100 if press["command"] == SomfyCommand.UP else 0
            )
        elif press["command"] == SomfyCommand.UP:
            self._position_estimator.start("up", 100)
            self._start_tracking()
        else:
            self._position_estimator.start("down", 0)
            self._start_tracking()
        self.async_write_ha_state()
        self._notify_parent_groups()

    def _cancel_tilt_tracking(self) -> None:
        """Freeze the tilt estimate of a remote-driven movement."""
        if self._tilt_track_task is not None and not self._tilt_track_task.done():
            self._tilt_track_task.cancel()
        self._tilt_track_task = None
        self._tilt_track = None

    def _start_tilt_tracking(self, target: int) -> None:
        """Track a tilt movement started by a physical remote."""
        self._cancel_tilt_tracking()
        self._tilt_track = {
            "start": time.monotonic(),
            "from": self._tilt,
            "target": target,
        }
        self._tilt_track_task = self._hass.async_create_task(
            self._async_tilt_tracking_tick()
        )

    async def _async_tilt_tracking_tick(self) -> None:
        """Interpolate the tilt estimate while a remote tilt runs."""
        track = self._tilt_track
        assert track is not None
        delta = track["target"] - track["from"]
        duration = abs(delta) / 100.0 * self._tilt_time_ms / 1000.0
        while duration > 0:
            await asyncio.sleep(_TILT_TIMED_TICK_S)
            if self._tilt_track is not track:
                return  # superseded or cancelled
            elapsed = time.monotonic() - track["start"]
            if elapsed >= duration:
                break
            self._tilt = _clamp(
                round(track["from"] + delta * (elapsed / duration))
            )
            self.async_write_ha_state()
        self._tilt = _clamp(track["target"])
        self._tilt_track = None
        self._tilt_track_task = None
        self.async_write_ha_state()

    def async_update_sensor_state(self, payload: int, device: str | None) -> None:
        """Update wind/sun attributes from a linked sensor frame.

        The motor may act on its own after a wind/sun alarm (awning wind
        protection); the position estimate is intentionally not changed
        here because the reaction depends on the motor configuration.
        """
        self._last_rx_rolling_code = payload
        self._last_rx_device = device
        self._wind = bool(payload & SENSOR_WIND_BIT)
        self._sun = bool(payload & SENSOR_SUN_BIT)
        self.async_write_ha_state()

    def async_update_from_rx(
        self,
        command: SomfyCommand | None,
        rolling_code: int,
        device: str | None,
        step: int = 0,
        repeat: int = 1,
    ) -> None:
        """Update the state from a received RTS frame (physical remote)."""
        if self._invert and command is not None:
            # The motor runs upside down: swap the button semantics.
            command = _INVERT_COMMANDS.get(command, command)
        self._last_rx_rolling_code = rolling_code
        self._last_rx_device = device
        self._last_rx_step = step or None

        if (
            self._tilt_supported
            and self._tilt_timed
            and command in (SomfyCommand.UP, SomfyCommand.DOWN)
        ):
            # A tilt motor reads a long press as tilt and a short press
            # as lift; resolve once the press ends.
            self._track_remote_press(command, rolling_code, repeat)
            self.async_write_ha_state()
            return
        if command in (SomfyCommand.STOP, SomfyCommand.MY):
            self._cancel_press()
            self._cancel_tilt_tracking()
        if command == SomfyCommand.UP:
            self._position_estimator.start("up", 100)
            self._start_tracking()
            if self._is_group:
                self._track_members_movement("up", 100)
        elif command == SomfyCommand.DOWN:
            self._position_estimator.start("down", 0)
            self._start_tracking()
            if self._is_group:
                self._track_members_movement("down", 0)
        elif command in (SomfyCommand.STOP, SomfyCommand.MY):
            if (
                command == SomfyCommand.MY
                and repeat < SETMY_REPEATS
                and self._my_position is not None
                and not self._position_estimator.is_moving
            ):
                if self._simulate_my:
                    # The motor ignores MY (no memorized favorite):
                    # Home Assistant drives it to its own favorite.
                    self._hass.async_create_task(
                        self.async_goto_my_position()
                    )
                else:
                    # Native: the motor heads to its memorized
                    # favorite, assumed to match my_position.
                    self._native_my_tracking()
            else:
                # STOP, a long MY (memorize) or an MY while moving:
                # the motor stops, the estimate freezes where it is.
                self._position_estimator.stop()
                self._stop_tracking()
                if self._is_group:
                    self._stop_members_tracking()
        elif command == SomfyCommand.STEP_UP:
            self._apply_rx_step(up=True)
            if self._is_group:
                self._track_members_tilt_step(up=True)
        elif command == SomfyCommand.STEP_DOWN:
            self._apply_rx_step(up=False)
            if self._is_group:
                self._track_members_tilt_step(up=False)
        self.async_write_ha_state()
        self._notify_parent_groups()
