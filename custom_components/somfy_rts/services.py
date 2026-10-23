"""Services for the Somfy RTS integration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

# probatio is Home Assistant's validation library, a drop-in
# successor of voluptuous (same API surface).
import probatio as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from . import address_manager, rf
from .const import (
    CONF_ADDRESS,
    CONF_GROUP_ADDRESSES,
    CONF_LINKED_REMOTE_ADDRESSES,
    CONF_MY_POSITION,
    DEFAULT_FRAME_REPEATS,
    DOMAIN,
    PROG_MODE_JOG_S,
    PROG_MODE_REPEATS,
    PROG_PAIR_SETTLE_S,
    SERVICE_ADD_TO_GROUP,
    SERVICE_ENTER_PROGRAMMING_MODE,
    SERVICE_GOTO_MY_POSITION,
    SERVICE_LINK_REMOTE,
    SERVICE_MEMORIZE_MY_POSITION,
    SERVICE_RECALIBRATE,
    SERVICE_REMOVE_FROM_GROUP,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_PROG,
    SERVICE_SEND_STEP_COMMAND,
    SERVICE_SET_MY_POSITION,
    SERVICE_SET_POSITION,
    SERVICE_SET_TILT_POSITION,
    SERVICE_UNLINK_REMOTE,
    SomfyCommand,
    SomfyProtocol,
)
from .cover import SomfyRTSCover
from .rolling_code import RollingCodeStore

_LOGGER = logging.getLogger(__name__)

REMOTE_ADDRESS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required("remote_address"): str,
    }
)

POSITION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required("position"): vol.All(int, vol.Range(min=0, max=100)),
    }
)

TILT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required("tilt_position"): vol.All(int, vol.Range(min=0, max=100)),
    }
)

MY_POSITION_SCHEMA = POSITION_SCHEMA
ENTITY_ONLY_SCHEMA = vol.Schema({vol.Required(CONF_ENTITY_ID): cv.entity_id})

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required("command"): vol.In(
            [
                SomfyCommand.UP.value,
                SomfyCommand.DOWN.value,
                SomfyCommand.MY.value,
                SomfyCommand.STOP.value,
                SomfyCommand.PROG.value,
                SomfyCommand.STEP_UP.value,
                SomfyCommand.STEP_DOWN.value,
            ]
        ),
        vol.Optional("repeat", default=3): vol.All(int, vol.Range(min=0, max=50)),
    }
)

SEND_STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required("direction"): vol.In(["up", "down"]),
        vol.Required("step_size"): vol.All(int, vol.Range(min=1, max=127)),
        vol.Optional("repeat", default=3): vol.All(int, vol.Range(min=0, max=50)),
    }
)

GROUP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_ids,
        vol.Required("group"): cv.entity_id,
    }
)


def _find_entity(hass: HomeAssistant, entity_id: str) -> SomfyRTSCover:
    """Find a Somfy RTS entity across all config entries."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            continue
        entity: SomfyRTSCover | None = runtime.entities.get(entity_id)
        if entity is not None:
            return entity
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="entity_not_found",
        translation_placeholders={"entity_id": entity_id},
    )


def _find_device(
    hass: HomeAssistant, entity: SomfyRTSCover
) -> tuple[ConfigEntry, list[dict[str, Any]], dict[str, Any]]:
    """Return the entity's entry, devices list and device config."""
    entry: ConfigEntry = entity._entry
    devices = [dict(d) for d in entry.data.get("devices", [])]
    device = next(
        (d for d in devices if str(d.get(CONF_ADDRESS)) == entity._address),
        None,
    )
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entity_not_found",
            translation_placeholders={"entity_id": entity.entity_id},
        )
    return entry, devices, device


def _persist_devices(
    hass: HomeAssistant, entry: ConfigEntry, devices: list[dict[str, Any]]
) -> None:
    """Write the updated devices list into the config entry."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "devices": devices}
    )


def _update_linked_remotes(
    hass: HomeAssistant, entity: SomfyRTSCover, address: str, add: bool
) -> None:
    """Persist linked remote addresses on the entity's config entry."""
    entry, devices, device = _find_device(hass, entity)

    linked = [str(a) for a in device.get(CONF_LINKED_REMOTE_ADDRESSES, [])]
    if add:
        if address in linked:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="address_already_linked",
                translation_placeholders={
                    "entity_id": entity.entity_id,
                    "remote_address": address,
                },
            )
        linked.append(address)
    else:
        if address not in linked:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="address_not_linked",
                translation_placeholders={
                    "entity_id": entity.entity_id,
                    "remote_address": address,
                },
            )
        linked.remove(address)
    device[CONF_LINKED_REMOTE_ADDRESSES] = linked

    _persist_devices(hass, entry, devices)
    entity.async_update_linked_remotes(linked)


def _require_cover_method(
    entity: SomfyRTSCover, method_name: str
) -> Callable[..., Any]:
    """Ensure the entity is a cover exposing the given method."""
    method = getattr(entity, method_name, None)
    if not callable(method):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_supported",
            translation_placeholders={"entity_id": entity.entity_id},
        )
    handler: Callable[..., Any] = method
    return handler


def _validate_80bit(entity: SomfyRTSCover) -> None:
    """Ensure the entity's device uses the 80-bit protocol."""

    if str(getattr(entity, "_protocol", SomfyProtocol.RTS_56BIT.value)) != str(
        SomfyProtocol.RTS_80BIT
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="requires_80bit",
            translation_placeholders={"entity_id": entity.entity_id},
        )


async def _async_send_prog_press(
    hass: HomeAssistant, entity: SomfyRTSCover, frame_repeats: int
) -> None:
    """Send a PROG press to the entity's device address.

    A short press (3 repeats) toggles the pairing while the motor is
    in programming mode; a long press (35 repeats) enters it.
    """
    store = hass.data.get(DOMAIN, {}).get("rolling_codes")
    if store is None:
        store = RollingCodeStore(hass)
        hass.data.setdefault(DOMAIN, {})["rolling_codes"] = store
    address = entity._address_int
    rolling_code = await store.async_get_next(address)
    try:
        await rf.async_send_command(
            hass,
            entity._transmitter_id,
            address,
            SomfyCommand.PROG,
            rolling_code,
            protocol=entity._protocol,
            frame_repeats=frame_repeats,
        )
    except HomeAssistantError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    # Register for loopback filtering, like every transmission.
    recent_tx = hass.data.setdefault(DOMAIN, {}).setdefault("recent_tx", {})
    recent_tx[(address, rolling_code)] = time.monotonic()

_GroupMember = tuple[
    SomfyRTSCover,
    ConfigEntry,
    "list[dict[str, Any]]",
    "dict[str, Any]",
    "list[str]",
]

async def _async_change_group_membership(
    hass: HomeAssistant, call: ServiceCall, add: bool
) -> None:
    """Pair covers to (or unpair them from) an RTS group address.

    RTS pairing is a toggle: the motor enters its programming mode
    on a long PROG press from its own paired address, then a short
    PROG from the group address adds that address if it is new,
    or removes it if it is already paired. The bookkeeping in the
    config entry follows the requested direction.
    """
    group_entity = _find_entity(hass, call.data["group"])
    if not bool(getattr(group_entity, "_is_group", False)):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_a_group",
            translation_placeholders={"entity_id": group_entity.entity_id},
        )
    group_address = str(getattr(group_entity, "_address", ""))

    member_ids = call.data[CONF_ENTITY_ID]
    if isinstance(member_ids, str):
        member_ids = [member_ids]

    members: list[_GroupMember] = []
    for member_id in member_ids:
        entity = _find_entity(hass, member_id)
        if entity is group_entity:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="group_in_itself",
                translation_placeholders={"group": group_entity.entity_id},
            )
        entry, devices, device = _find_device(hass, entity)
        groups = [str(a) for a in device.get(CONF_GROUP_ADDRESSES, [])]
        if add and group_address in groups:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="already_in_group",
                translation_placeholders={
                "entity_id": entity.entity_id,
                "group": group_entity.entity_id,
            },
            )
        if not add and group_address not in groups:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_in_group",
                translation_placeholders={
                "entity_id": entity.entity_id,
                "group": group_entity.entity_id,
            },
            )
        members.append((entity, entry, devices, device, groups))

    for entity, entry, devices, device, groups in members:
        # Programming mode on the member, then the group PROG press.
        await _async_send_prog_press(hass, entity, PROG_MODE_REPEATS)
        await asyncio.sleep(PROG_MODE_JOG_S)
        await _async_send_prog_press(hass, group_entity, DEFAULT_FRAME_REPEATS)
        await asyncio.sleep(PROG_PAIR_SETTLE_S)
        if add:
            groups.append(group_address)
        else:
            groups.remove(group_address)
        device[CONF_GROUP_ADDRESSES] = groups
        _persist_devices(hass, entry, devices)
        entity.async_update_group_addresses(groups)
    _LOGGER.info(
        "%s %s %s the group %s",
        "Added" if add else "Removed",
        ", ".join(str(m[0].entity_id) for m in members),
        "to" if add else "from",
        group_entity.entity_id,
    )

async def async_setup_services(hass: HomeAssistant) -> None:
    """Register services (called once from async_setup)."""

    async def handle_link_remote(call: ServiceCall) -> None:
        """Link a physical remote so its usage updates the entity state."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        address = call.data["remote_address"].strip()
        if not address_manager.is_address_valid(address):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_address",
            )
        _update_linked_remotes(hass, entity, address, add=True)
        _LOGGER.info("Linked remote %s to %s", address, call.data[CONF_ENTITY_ID])

    async def handle_unlink_remote(call: ServiceCall) -> None:
        """Unlink a physical remote from an entity."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        address = call.data["remote_address"].strip()
        _update_linked_remotes(hass, entity, address, add=False)
        _LOGGER.info("Unlinked remote %s from %s", address, call.data[CONF_ENTITY_ID])

    async def handle_set_my_position(call: ServiceCall) -> None:
        """Store the My (favorite) position of a cover."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        set_method = _require_cover_method(entity, "async_set_my_position")
        entry, devices, device = _find_device(hass, entity)
        device[CONF_MY_POSITION] = int(call.data["position"])
        _persist_devices(hass, entry, devices)
        set_method(call.data["position"])

    async def handle_goto_my_position(call: ServiceCall) -> None:
        """Move a cover to its configured My (favorite) position."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        goto = _require_cover_method(entity, "async_goto_my_position")
        if getattr(entity, "_my_position", None) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="my_position_not_set",
                translation_placeholders={"entity_id": entity.entity_id},
            )
        await goto()

    async def handle_enter_programming_mode(call: ServiceCall) -> None:
        """Long PROG press: the motor enters programming mode."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        await _async_send_prog_press(hass, entity, PROG_MODE_REPEATS)

    async def handle_send_prog(call: ServiceCall) -> None:
        """Short PROG press: pairs/unpairs in programming mode."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        await _async_send_prog_press(hass, entity, DEFAULT_FRAME_REPEATS)

    async def handle_memorize_my_position(call: ServiceCall) -> None:
        """Long MY press + config record: the native favorite."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        memorize = _require_cover_method(entity, "async_memorize_my_position")
        await memorize()
        position = entity.current_cover_position
        entity.async_set_my_position(position)
        entry, devices, device = _find_device(hass, entity)
        device[CONF_MY_POSITION] = position
        _persist_devices(hass, entry, devices)

    async def handle_recalibrate(call: ServiceCall) -> None:
        """Re-anchor the position estimate with a full close/open cycle."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        recal = _require_cover_method(entity, "async_recalibrate")
        recal()

    async def handle_set_position(call: ServiceCall) -> None:
        """Realign the position estimate without moving the motor."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        method = _require_cover_method(entity, "async_set_current_position")
        method(call.data["position"])

    async def handle_set_tilt_position(call: ServiceCall) -> None:
        """Realign the tilt estimate without moving the motor."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        if getattr(entity, "current_cover_tilt_position", None) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="tilt_not_supported",
                translation_placeholders={"entity_id": entity.entity_id},
            )
        entity.async_set_current_tilt_position(call.data["tilt_position"])

    async def handle_send_command(call: ServiceCall) -> None:
        """Send a raw RTS command on the entity's address."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        send = _require_cover_method(entity, "async_send_raw_command")
        command = SomfyCommand(call.data["command"])
        if command in (SomfyCommand.STEP_UP, SomfyCommand.STEP_DOWN):
            _validate_80bit(entity)
        try:
            await send(command, frame_repeats=call.data["repeat"])
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={
                    "entity_id": entity.entity_id,
                    "error": str(err),
                },
            ) from err

    async def handle_send_step_command(call: ServiceCall) -> None:
        """Send a step command with an explicit step size."""
        entity = _find_entity(hass, call.data[CONF_ENTITY_ID])
        send = _require_cover_method(entity, "async_send_raw_command")
        _validate_80bit(entity)
        command = (
            SomfyCommand.STEP_UP
            if call.data["direction"] == "up"
            else SomfyCommand.STEP_DOWN
        )
        try:
            await send(
                command,
                frame_repeats=call.data["repeat"],
                step_size=call.data["step_size"],
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={
                    "entity_id": entity.entity_id,
                    "error": str(err),
                },
            ) from err

    async def handle_add_to_group(call: ServiceCall) -> None:
        """Pair covers to an RTS group from the HA interface."""
        await _async_change_group_membership(hass, call, add=True)

    async def handle_remove_from_group(call: ServiceCall) -> None:
        """Unpair covers from an RTS group from the HA interface."""
        await _async_change_group_membership(hass, call, add=False)

    hass.services.async_register(
        DOMAIN, SERVICE_LINK_REMOTE, handle_link_remote, schema=REMOTE_ADDRESS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNLINK_REMOTE, handle_unlink_remote, schema=REMOTE_ADDRESS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_MY_POSITION, handle_set_my_position, schema=MY_POSITION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ENTER_PROGRAMMING_MODE,
        handle_enter_programming_mode, schema=ENTITY_ONLY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_PROG,
        handle_send_prog, schema=ENTITY_ONLY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_MEMORIZE_MY_POSITION,
        handle_memorize_my_position, schema=ENTITY_ONLY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECALIBRATE,
        handle_recalibrate, schema=ENTITY_ONLY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GOTO_MY_POSITION,
        handle_goto_my_position,
        schema=vol.Schema({vol.Required(CONF_ENTITY_ID): cv.entity_id}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_POSITION, handle_set_position, schema=POSITION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_TILT_POSITION, handle_set_tilt_position, schema=TILT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_COMMAND, handle_send_command, schema=SEND_COMMAND_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_STEP_COMMAND, handle_send_step_command, schema=SEND_STEP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TO_GROUP, handle_add_to_group, schema=GROUP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_FROM_GROUP, handle_remove_from_group, schema=GROUP_SCHEMA
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Remove services when the last config entry is unloaded."""
    for service in (
        SERVICE_LINK_REMOTE,
        SERVICE_RECALIBRATE,
        SERVICE_ENTER_PROGRAMMING_MODE,
        SERVICE_MEMORIZE_MY_POSITION,
        SERVICE_SEND_PROG,
        SERVICE_UNLINK_REMOTE,
        SERVICE_SET_MY_POSITION,
        SERVICE_GOTO_MY_POSITION,
        SERVICE_SET_POSITION,
        SERVICE_SET_TILT_POSITION,
        SERVICE_SEND_COMMAND,
        SERVICE_SEND_STEP_COMMAND,
        SERVICE_ADD_TO_GROUP,
        SERVICE_REMOVE_FROM_GROUP,
    ):
        hass.services.async_remove(DOMAIN, service)
