"""Config flow for the RTS integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

# probatio is Home Assistant's validation library, a drop-in
# successor of voluptuous (same API surface).
import probatio as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.selector import SelectOptionDict

from . import address_manager, rf
from .const import (
    CONF_ADDRESS,
    CONF_BRIGHTNESS_STEP_PERCENT,
    CONF_CLOSE_TIME_MS,
    CONF_DEVICE_INDEX,
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_ENABLE_RX,
    CONF_GROUP,
    CONF_GROUP_ADDRESSES,
    CONF_INVERT,
    CONF_INVERT_POSITION,
    CONF_LINKED_REMOTE_ADDRESSES,
    CONF_MY_POSITION,
    CONF_NAME,
    CONF_OPEN_TIME_MS,
    CONF_PROTOCOL,
    CONF_SENSORS,
    CONF_SIMULATE_MY,
    CONF_STEP_SIZE,
    CONF_STOP_METHOD,
    CONF_TILT,
    CONF_TILT_STEP_PERCENT,
    CONF_TILT_TIME_MS,
    CONF_TOGGLE_COMMAND,
    CONF_TRANSMITTER_ID,
    COVER_DEVICE_TYPES,
    DEFAULT_BRIGHTNESS_STEP_PERCENT,
    DEFAULT_CLOSE_TIME_MS,
    DEFAULT_ENABLE_RX,
    DEFAULT_FRAME_REPEATS,
    DEFAULT_OPEN_TIME_MS,
    DEFAULT_STEP_SIZE,
    DEFAULT_TILT_STEP_PERCENT,
    DEFAULT_TILT_TIME_MS,
    DOMAIN,
    FLIP_COMMAND_EXCLUDED_DEVICE_TYPES,
    PROG_MODE_REPEATS,
    STOP_METHOD_MY,
    STOP_METHOD_PULSE,
    TILT_DEVICE_TYPES,
    SomfyCommand,
    SomfyDeviceType,
    SomfyProtocol,
    device_type_label,
)
from .rolling_code import RollingCodeStore

_LOGGER = logging.getLogger(__name__)

def device_type_options(hass: HomeAssistant) -> list[SelectOptionDict]:
    """Device type dropdown entries, in the user's language."""
    return [
        SelectOptionDict(
            value=device_type.value,
            label=device_type_label(hass, device_type),
        )
        for device_type in SomfyDeviceType
    ]


TOGGLE_COMMAND_OPTIONS = [
    SelectOptionDict(value=SomfyCommand.MY.value, label="MY"),
    SelectOptionDict(value=SomfyCommand.UP.value, label="UP"),
    SelectOptionDict(value=SomfyCommand.DOWN.value, label="DOWN"),
]

def step_add_device_schema(hass: HomeAssistant) -> vol.Schema:
    """Add-device form, with device types in the user's language."""
    return vol.Schema(
        {
            vol.Required(CONF_NAME): str,
            vol.Optional(CONF_ADDRESS): str,
            vol.Required(CONF_DEVICE_TYPE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=device_type_options(hass),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_PROTOCOL, default=SomfyProtocol.RTS_56BIT.value
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[p.value for p in SomfyProtocol],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(CONF_OPEN_TIME_MS, default=DEFAULT_OPEN_TIME_MS): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1000, max=600000, step=500, unit_of_measurement="ms")
            ),
            vol.Optional(CONF_CLOSE_TIME_MS, default=DEFAULT_CLOSE_TIME_MS): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1000, max=600000, step=500, unit_of_measurement="ms")
            ),
        }
    )


# A group is a grouping of covers, not a device: no travel times,
# no per-type options. Its members are chosen on the next step.
def step_add_group_schema(hass: HomeAssistant) -> vol.Schema:
    """Add-group form: cover types only, in the user's language."""
    return vol.Schema(
        {
            vol.Required(CONF_NAME): str,
            vol.Required(
                CONF_DEVICE_TYPE, default=SomfyDeviceType.ROLLER_SHUTTER.value
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=device_type.value,
                            label=device_type_label(hass, device_type),
                        )
                        for device_type in SomfyDeviceType
                        if device_type in COVER_DEVICE_TYPES
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_ADDRESS): str,
        }
    )


def _device_option_fields(device: dict[str, Any]) -> dict[Any, Any]:
    """Return the fields to show for a device, based on its type and
    protocol (used by both the add and the edit flows)."""
    fields: dict[Any, Any] = {}
    device_type = SomfyDeviceType(device[CONF_DEVICE_TYPE])
    protocol = SomfyProtocol(device.get(CONF_PROTOCOL, SomfyProtocol.RTS_56BIT.value))
    if device_type in COVER_DEVICE_TYPES:
        if device_type not in FLIP_COMMAND_EXCLUDED_DEVICE_TYPES:
            fields[
                vol.Optional(
                    CONF_INVERT, default=bool(device.get(CONF_INVERT, False))
                )
            ] = bool
        fields[
            vol.Optional(
                CONF_MY_POSITION,
                default=device.get(CONF_MY_POSITION, 50),
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
        )
        fields[
            vol.Optional(
                CONF_INVERT_POSITION,
                default=bool(device.get(CONF_INVERT_POSITION, False)),
            )
        ] = bool
        fields[
            vol.Optional(
                CONF_SIMULATE_MY,
                default=bool(device.get(CONF_SIMULATE_MY, True)),
            )
        ] = bool
        fields[
            vol.Optional(
                CONF_STOP_METHOD,
                default=str(device.get(CONF_STOP_METHOD, STOP_METHOD_MY)),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=STOP_METHOD_MY, label="MY (default)"),
                    SelectOptionDict(value=STOP_METHOD_PULSE, label="Opposite pulse (motors without MY)"),
                ],
                mode=selector.SelectSelectorMode.LIST,
            )
        )
    if device_type in (SomfyDeviceType.SOCKET, SomfyDeviceType.LIGHT):
        fields[
            vol.Optional(
                CONF_TOGGLE_COMMAND,
                default=str(device.get(CONF_TOGGLE_COMMAND, SomfyCommand.MY.value)),
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=TOGGLE_COMMAND_OPTIONS,
                mode=selector.SelectSelectorMode.LIST,
            )
        )
    if device_type in TILT_DEVICE_TYPES:
        fields[
            vol.Optional(CONF_TILT, default=bool(device.get(CONF_TILT, False)))
        ] = bool
        if protocol == SomfyProtocol.RTS_80BIT:
            fields[
                vol.Required(
                    CONF_TILT_STEP_PERCENT,
                    default=int(
                        device.get(CONF_TILT_STEP_PERCENT, DEFAULT_TILT_STEP_PERCENT)
                    ),
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=50, step=1, unit_of_measurement="%"
                )
            )
        else:
            # 56-bit tilt motor: timed Up/Down commands.
            fields[
                vol.Required(
                    CONF_TILT_TIME_MS,
                    default=int(device.get(CONF_TILT_TIME_MS, DEFAULT_TILT_TIME_MS)),
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=500, max=600000, step=100, unit_of_measurement="ms"
                )
            )
    if protocol == SomfyProtocol.RTS_80BIT:
        if device_type == SomfyDeviceType.LIGHT:
            fields[
                vol.Required(
                    CONF_BRIGHTNESS_STEP_PERCENT,
                    default=int(
                        device.get(
                            CONF_BRIGHTNESS_STEP_PERCENT, DEFAULT_BRIGHTNESS_STEP_PERCENT
                        )
                    ),
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=50, step=1, unit_of_measurement="%"
                )
            )
        fields[
            vol.Required(
                CONF_STEP_SIZE,
                default=int(device.get(CONF_STEP_SIZE, DEFAULT_STEP_SIZE)),
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=127, step=1)
        )
    return fields


# Option fields are grouped into form sections for easier navigation.
# The section keys match the "sections" translations of the flow steps.
SECTION_MOVEMENT = "movement"
SECTION_POSITION = "position"
SECTION_TILT = "tilt"
SECTION_RADIO = "radio"
SECTION_REMOTES = "remotes"

_SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    SECTION_MOVEMENT: (CONF_OPEN_TIME_MS, CONF_CLOSE_TIME_MS),
    SECTION_POSITION: (
        CONF_INVERT,
        CONF_INVERT_POSITION,
        CONF_MY_POSITION,
        CONF_SIMULATE_MY,
        CONF_STOP_METHOD,
    ),
    SECTION_TILT: (CONF_TILT, CONF_TILT_STEP_PERCENT, CONF_TILT_TIME_MS),
    SECTION_RADIO: (
        CONF_TOGGLE_COMMAND,
        CONF_BRIGHTNESS_STEP_PERCENT,
        CONF_STEP_SIZE,
    ),
    SECTION_REMOTES: (CONF_LINKED_REMOTE_ADDRESSES,),
}


def _merge_section_values(user_input: dict[str, Any]) -> dict[str, Any]:
    """Flatten the section payloads back into plain field names."""
    merged: dict[str, Any] = {}
    for key, value in user_input.items():
        if isinstance(value, dict):
            merged.update(value)
        else:
            merged[key] = value
    return merged


def _sectioned_fields(fields: dict[Any, Any]) -> dict[str, dict[Any, Any]]:
    """Bucket option fields into their form sections, in order."""
    where = {
        name: section_key
        for section_key, names in _SECTION_FIELDS.items()
        for name in names
    }
    sections: dict[str, dict[Any, Any]] = {key: {} for key in _SECTION_FIELDS}
    for key, value in fields.items():
        sections[where[key.schema]][key] = value
    return {key: value for key, value in sections.items() if value}


def _sectioned_schema(
    fields: dict[Any, Any],
    leading: dict[Any, Any] | None = None,
) -> vol.Schema:
    """Wrap option fields into form sections.

    Leading fields (e.g. the name) render above the sections.
    A lone section renders flat: a collapsible wrapper around the
    only group adds a click without adding navigation. Sections
    are never collapsed.
    """
    sectioned = _sectioned_fields(fields)
    schema: dict[Any, Any] = dict(leading or {})
    if len(sectioned) == 1:
        schema.update(next(iter(sectioned.values())))
    else:
        for key, section_fields in sectioned.items():
            schema[vol.Required(key)] = section(vol.Schema(section_fields))
    return vol.Schema(schema)


def _device_select_options(
    devices: list[dict[str, Any]],
) -> list[SelectOptionDict]:
    """Dropdown entries for picking a device in a flow step."""
    return [
        {
            "value": str(i),
            "label": f"{d.get(CONF_NAME, '?')} ({d.get(CONF_ADDRESS, '?')})",
        }
        for i, d in enumerate(devices)
    ]

async def async_send_prog_press(
    hass: HomeAssistant,
    transmitter_id: str,
    device: dict[str, Any],
    repeats: int,
) -> None:
    """Send a PROG press of the given length on a device address."""
    address = int(device[CONF_ADDRESS])
    store = hass.data.get(DOMAIN, {}).get("rolling_codes")
    if store is None:
        store = RollingCodeStore(hass)
        hass.data.setdefault(DOMAIN, {})["rolling_codes"] = store
    rolling_code = await store.async_get_next(address)
    await rf.async_send_command(
        hass,
        transmitter_id,
        address,
        SomfyCommand.PROG,
        rolling_code,
        protocol=SomfyProtocol(
            device.get(CONF_PROTOCOL, SomfyProtocol.RTS_56BIT)
        ),
        frame_repeats=repeats,
    )
    recent_tx = hass.data.setdefault(DOMAIN, {}).setdefault("recent_tx", {})
    recent_tx[(address, rolling_code)] = time.monotonic()


async def async_hold_prog_press(
    hass: HomeAssistant,
    transmitter_id: str,
    device: dict[str, Any],
) -> None:
    """Send the long PROG press and let it finish on air.

    The gateway transmits non-blocking: a press sent right behind the
    burst (the pairing press on the next validation) would queue
    behind it and reach the motor seconds later. Holding here makes
    the next step appear once the air is free.
    """
    protocol = SomfyProtocol(device.get(CONF_PROTOCOL, SomfyProtocol.RTS_56BIT.value))
    await async_send_prog_press(hass, transmitter_id, device, PROG_MODE_REPEATS)
    await asyncio.sleep(rf.press_air_time_s(SomfyCommand.PROG, protocol, PROG_MODE_REPEATS))


def group_member_options(
    group_address: str, devices: list[dict[str, Any]]
) -> tuple[list[SelectOptionDict], list[str]]:
    """Selectable covers and their current group membership."""
    options: list[SelectOptionDict] = []
    current: list[str] = []
    for i, d in enumerate(devices):
        if str(d.get(CONF_ADDRESS)) == group_address:
            continue
        if SomfyDeviceType(str(d.get(CONF_DEVICE_TYPE))) not in COVER_DEVICE_TYPES:
            continue
        options.append(
            SelectOptionDict(
                value=str(i),
                label=f"{d.get(CONF_NAME, '?')} ({d.get(CONF_ADDRESS, '?')})",
            )
        )
        if group_address in [
            str(a) for a in d.get(CONF_GROUP_ADDRESSES, [])
        ]:
            current.append(str(i))
    return options, current


def _finalize_device_options(
    device: dict[str, Any], user_input: dict[str, Any]
) -> dict[str, Any]:
    """Merge the conditional options into a complete device config."""
    device_type = SomfyDeviceType(device[CONF_DEVICE_TYPE])
    protocol = SomfyProtocol(device.get(CONF_PROTOCOL, SomfyProtocol.RTS_56BIT.value))
    is_receiver = device_type in (SomfyDeviceType.SOCKET, SomfyDeviceType.LIGHT)
    is_cover = device_type in COVER_DEVICE_TYPES
    is_light = device_type == SomfyDeviceType.LIGHT
    is_80bit = protocol == SomfyProtocol.RTS_80BIT
    is_tilt_capable = device_type in TILT_DEVICE_TYPES
    is_flip_excluded = device_type in FLIP_COMMAND_EXCLUDED_DEVICE_TYPES
    is_cover_type = device_type in COVER_DEVICE_TYPES
    complete = dict(device)
    complete[CONF_TOGGLE_COMMAND] = (
        user_input.get(
            CONF_TOGGLE_COMMAND,
            str(complete.get(CONF_TOGGLE_COMMAND, SomfyCommand.MY.value)),
        )
        if is_receiver
        else SomfyCommand.MY.value
    )
    complete[CONF_INVERT] = (
        bool(user_input.get(CONF_INVERT, complete.get(CONF_INVERT, False)))
        if (is_cover and not is_flip_excluded)
        else False
    )
    complete[CONF_INVERT_POSITION] = (
        bool(
            user_input.get(
                CONF_INVERT_POSITION, complete.get(CONF_INVERT_POSITION, False)
            )
        )
        if is_cover
        else False
    )
    if is_cover:
        my_position = user_input.get(
            CONF_MY_POSITION, complete.get(CONF_MY_POSITION, 50)
        )
        if my_position is not None:
            complete[CONF_MY_POSITION] = int(my_position)
    complete[CONF_TILT] = (
        bool(user_input.get(CONF_TILT, complete.get(CONF_TILT, False)))
        if is_tilt_capable
        else False
    )
    complete[CONF_TILT_STEP_PERCENT] = int(
        user_input.get(
            CONF_TILT_STEP_PERCENT,
            int(complete.get(CONF_TILT_STEP_PERCENT, DEFAULT_TILT_STEP_PERCENT)),
        )
        if (is_80bit and is_tilt_capable)
        else DEFAULT_TILT_STEP_PERCENT
    )
    complete[CONF_STEP_SIZE] = int(
        user_input.get(
            CONF_STEP_SIZE,
            int(complete.get(CONF_STEP_SIZE, DEFAULT_STEP_SIZE)),
        )
        if is_80bit
        else DEFAULT_STEP_SIZE
    )
    complete[CONF_TILT_TIME_MS] = int(
        user_input.get(
            CONF_TILT_TIME_MS,
            int(complete.get(CONF_TILT_TIME_MS, DEFAULT_TILT_TIME_MS)),
        )
        if is_tilt_capable
        else DEFAULT_TILT_TIME_MS
    )
    complete[CONF_SIMULATE_MY] = (
        bool(
            user_input.get(
                CONF_SIMULATE_MY, complete.get(CONF_SIMULATE_MY, True)
            )
        )
        if is_cover_type
        else True
    )
    complete[CONF_STOP_METHOD] = (
        str(
            user_input.get(
                CONF_STOP_METHOD,
                complete.get(CONF_STOP_METHOD, STOP_METHOD_MY),
            )
        )
        if is_cover_type
        else STOP_METHOD_MY
    )
    complete[CONF_BRIGHTNESS_STEP_PERCENT] = int(
        user_input.get(
            CONF_BRIGHTNESS_STEP_PERCENT,
            int(
                complete.get(
                    CONF_BRIGHTNESS_STEP_PERCENT, DEFAULT_BRIGHTNESS_STEP_PERCENT
                )
            ),
        )
        if (is_80bit and is_light)
        else DEFAULT_BRIGHTNESS_STEP_PERCENT
    )
    return complete


class SomfyRTSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the RTS config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._transmitter_id: str | None = None
        self._devices: list[dict[str, Any]] = []
        self._sensors: list[dict[str, Any]] = []
        self._pending_device: dict[str, Any] | None = None
        self._group_index: int | None = None
        self._member_queue: list[tuple[int, bool]] = []
        self._member_pos: int = 0

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the RF transmitter to use."""
        # One config entry per transmitter: creating a second entry for
        # the same gateway would duplicate rolling code counters.
        used = {
            entry.data.get(CONF_TRANSMITTER_ID)
            for entry in self._async_current_entries()
        }
        transmitters = [
            t for t in rf.get_transmitters(self.hass) if t not in used
        ]
        if not transmitters:
            return self.async_abort(reason="no_transmitters")

        if user_input is not None:
            self._transmitter_id = user_input[CONF_TRANSMITTER_ID]
            return await self.async_step_menu()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TRANSMITTER_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=transmitters,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure the RF transmitter of the entry."""
        entry = self._get_reconfigure_entry()
        current = entry.data.get(CONF_TRANSMITTER_ID)
        used = {
            e.data.get(CONF_TRANSMITTER_ID)
            for e in self._async_current_entries()
            if e.entry_id != entry.entry_id
        }
        transmitters = [
            t
            for t in rf.get_transmitters(self.hass)
            if t == current or t not in used
        ]
        if not transmitters:
            return self.async_abort(reason="no_transmitters")
        if user_input is not None:
            new_transmitter = user_input[CONF_TRANSMITTER_ID]
            data = dict(entry.data)
            data[CONF_TRANSMITTER_ID] = new_transmitter
            data[CONF_DEVICES] = [
                {**d, CONF_TRANSMITTER_ID: new_transmitter}
                for d in entry.data.get(CONF_DEVICES, [])
            ]
            data[CONF_SENSORS] = [
                {**s, CONF_TRANSMITTER_ID: new_transmitter}
                for s in entry.data.get(CONF_SENSORS, [])
            ]
            return self.async_update_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TRANSMITTER_ID, default=current
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=transmitters,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            description_placeholders={
                "transmitter": current or "",
            },
        )

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between adding another device and finishing."""
        return self.async_show_menu(
            step_id="menu",
            menu_options=["add_device", "add_group", "add_sensor", "manage_group", "done"],
        )

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the config entry."""
        return self.async_create_entry(
            title=f"RTS Gateway ({self._transmitter_id})",
            data={
                CONF_TRANSMITTER_ID: self._transmitter_id,
                CONF_ENABLE_RX: DEFAULT_ENABLE_RX,
                "devices": self._devices,
                "sensors": self._sensors,
            },
        )

    async def async_step_add_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a sun/wind RTS sensor (Sunis, Eolis, Soliris)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = str(user_input.get(CONF_ADDRESS) or "").strip()
            if not address:
                address = address_manager.generate_address(self.hass)
            used = address_manager.get_used_addresses(self.hass)
            if not address_manager.is_address_valid(address):
                errors[CONF_ADDRESS] = "invalid_address"
            elif (
                int(address) in used
                or any(d[CONF_ADDRESS] == address for d in self._devices)
                or any(s[CONF_ADDRESS] == address for s in self._sensors)
            ):
                errors[CONF_ADDRESS] = "address_in_use"
            else:
                self._sensors.append(
                    {
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_ADDRESS: address,
                        CONF_TRANSMITTER_ID: self._transmitter_id,
                    }
                )
                return await self.async_step_menu()

        return self.async_show_form(
            step_id="add_sensor",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Optional(CONF_ADDRESS): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "address_hint": address_manager.generate_address(self.hass),
            },
        )

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a new device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = str(user_input.get(CONF_ADDRESS) or "").strip()
            if not address:
                address = address_manager.generate_address(self.hass)
            if not address_manager.is_address_valid(address):
                errors[CONF_ADDRESS] = "invalid_address"
            elif (
                address_manager.is_address_used(self.hass, address)
                or any(d[CONF_ADDRESS] == address for d in self._devices)
                or any(s[CONF_ADDRESS] == address for s in self._sensors)
            ):
                errors[CONF_ADDRESS] = "address_in_use"
            else:
                self._pending_device = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_ADDRESS: address,
                    CONF_DEVICE_TYPE: user_input[CONF_DEVICE_TYPE],
                    CONF_TRANSMITTER_ID: self._transmitter_id,
                    CONF_PROTOCOL: user_input[CONF_PROTOCOL],
                    CONF_OPEN_TIME_MS: user_input[CONF_OPEN_TIME_MS],
                    CONF_CLOSE_TIME_MS: user_input[CONF_CLOSE_TIME_MS],
                }
                return await self.async_step_device_options()

        return self.async_show_form(
            step_id="add_device",
            data_schema=step_add_device_schema(self.hass),
            errors=errors,
            description_placeholders={
                "address_hint": address_manager.generate_address(self.hass),
            },
        )

    async def async_step_add_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create an RTS group: a grouping of covers, not a device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = str(user_input.get(CONF_ADDRESS) or "").strip()
            if not address:
                address = address_manager.generate_address(self.hass, group=True)
            if not address_manager.is_address_valid(address, group=True):
                errors[CONF_ADDRESS] = "invalid_address"
            elif (
                address_manager.is_address_used(self.hass, address)
                or any(d[CONF_ADDRESS] == address for d in self._devices)
                or any(s[CONF_ADDRESS] == address for s in self._sensors)
            ):
                errors[CONF_ADDRESS] = "address_in_use"
            else:
                # Lean config: no travel times, no per-type options.
                self._devices.append(
                    {
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_ADDRESS: address,
                        CONF_DEVICE_TYPE: user_input[CONF_DEVICE_TYPE],
                        CONF_TRANSMITTER_ID: self._transmitter_id,
                        CONF_PROTOCOL: SomfyProtocol.RTS_56BIT.value,
                        CONF_GROUP: True,
                    }
                )
                return await self.async_step_menu()
        return self.async_show_form(
            step_id="add_group",
            data_schema=step_add_group_schema(self.hass),
            errors=errors,
            description_placeholders={
                "address_hint": address_manager.generate_address(
                    self.hass, group=True
                ),
            },
        )

    async def async_step_manage_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the RTS group whose members are managed."""
        devices = self._devices
        if user_input is not None:
            self._group_index = int(user_input[CONF_DEVICE_INDEX])
            return await self.async_step_manage_group_members()
        group_options: list[SelectOptionDict] = [
            SelectOptionDict(
                value=str(i),
                label=f"{d.get(CONF_NAME, '?')} ({d.get(CONF_ADDRESS, '?')})",
            )
            for i, d in enumerate(devices)
            if d.get(CONF_GROUP)
        ]
        if not group_options:
            return await self.async_step_menu()
        return self.async_show_form(
            step_id="manage_group",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_INDEX): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=group_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_manage_group_members(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the covers that belong to the group."""
        assert self._group_index is not None
        devices = self._devices
        group = devices[self._group_index]
        group_address = str(group[CONF_ADDRESS])
        options, current = group_member_options(group_address, devices)
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = {str(v) for v in user_input.get(CONF_DEVICE_INDEX, [])}
            to_add = sorted(int(v) for v in selected - set(current))
            to_remove = sorted(int(v) for v in set(current) - selected)
            queue = [(index, True) for index in to_add]
            queue += [(index, False) for index in to_remove]
            if not queue:
                self._group_index = None
                return await self.async_step_menu()
            # One member at a time: each motor's memory must be open
            # (long PROG press) and the user confirms the jogs, like
            # the manual Somfy procedure.
            self._member_queue = queue
            self._member_pos = 0
            return await self.async_step_member_memory(None)
        return self.async_show_form(
            step_id="manage_group_members",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_INDEX, default=current
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "group": group.get(CONF_NAME, ""),
            },
        )

    async def async_step_member_memory(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open the member motor's memory and confirm the jog."""
        assert self._group_index is not None
        devices = self._devices
        group = devices[self._group_index]
        index, _add = self._member_queue[self._member_pos]
        member = devices[index]
        transmitter = self._transmitter_id or ""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("jogged"):
                try:
                    await async_send_prog_press(
                        self.hass, transmitter, group, DEFAULT_FRAME_REPEATS
                    )
                except HomeAssistantError as err:
                    _LOGGER.warning("Group PROG transmission failed: %s", err)
                    errors["base"] = "transmission_failed"
                if not errors:
                    return await self.async_step_member_pair_confirm(None)
            else:
                errors["base"] = "memory_not_opened"
        if errors.get("base") != "transmission_failed":
            # (Re)send the long PROG press that opens the motor memory,
            # held until the burst is done on air: the gateway queues
            # behind it, and the pairing press must not wait it out.
            try:
                await async_hold_prog_press(self.hass, transmitter, member)
            except HomeAssistantError as err:
                _LOGGER.warning("Group PROG transmission failed: %s", err)
                errors["base"] = "transmission_failed"
        return self.async_show_form(
            step_id="member_memory",
            data_schema=vol.Schema({vol.Required("jogged"): bool}),
            errors=errors,
            description_placeholders={
                "cover": member.get(CONF_NAME, ""),
                "group": group.get(CONF_NAME, ""),
            },
        )

    async def async_step_member_pair_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the group PROG press jogged the motor."""
        assert self._group_index is not None
        devices = self._devices
        group = devices[self._group_index]
        group_address = str(group[CONF_ADDRESS])
        index, add = self._member_queue[self._member_pos]
        member = devices[index]
        transmitter = self._transmitter_id or ""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("paired"):
                addresses = [
                    str(a) for a in member.get(CONF_GROUP_ADDRESSES, [])
                ]
                if add:
                    addresses.append(group_address)
                else:
                    addresses.remove(group_address)
                member[CONF_GROUP_ADDRESSES] = addresses
                self._member_pos += 1
                if self._member_pos < len(self._member_queue):
                    return await self.async_step_member_memory(None)
                self._member_queue = []
                self._member_pos = 0
                self._group_index = None
                return await self.async_step_menu()
            errors["base"] = "pair_not_confirmed"
            # Resend the group PROG press for a new confirmation.
            try:
                await async_send_prog_press(
                    self.hass, transmitter, group, DEFAULT_FRAME_REPEATS
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Group PROG transmission failed: %s", err)
                errors["base"] = "transmission_failed"
        return self.async_show_form(
            step_id="member_pair_confirm",
            data_schema=vol.Schema({vol.Required("paired"): bool}),
            errors=errors,
            description_placeholders={
                "cover": member.get(CONF_NAME, ""),
                "group": group.get(CONF_NAME, ""),
            },
        )

    async def async_step_device_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the parameters specific to the type and protocol."""
        assert self._pending_device is not None
        if user_input is not None:
            self._pending_device = _finalize_device_options(
                self._pending_device, _merge_section_values(user_input)
            )
            return await self.async_step_pair()

        assert self._pending_device is not None
        fields = _device_option_fields(self._pending_device)
        if not fields:
            # Nothing specific to this combination: pair right away.
            self._pending_device = _finalize_device_options(
                self._pending_device, {}
            )
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="device_options",
            data_schema=_sectioned_schema(fields),
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send the PROG command while the motor is in programming mode."""
        errors: dict[str, str] = {}
        if user_input is not None and self._pending_device is not None:
            assert self._pending_device is not None
            assert self._transmitter_id is not None
            address = int(self._pending_device[CONF_ADDRESS])
            try:
                store = self.hass.data.get(DOMAIN, {}).get("rolling_codes")
                if store is None:
                    # The component may not be fully set up yet.
                    store = RollingCodeStore(self.hass)
                    self.hass.data.setdefault(DOMAIN, {})["rolling_codes"] = store
                rolling_code = await store.async_get_next(address)
                await rf.async_send_command(
                    self.hass,
                    self._transmitter_id,
                    address,
                    SomfyCommand.PROG,
                    rolling_code,
                    protocol=SomfyProtocol(
                        self._pending_device.get(
                            CONF_PROTOCOL, SomfyProtocol.RTS_56BIT
                        )
                    ),
                )
                recent_tx = self.hass.data.setdefault(DOMAIN, {}).setdefault(
                    "recent_tx", {}
                )
                recent_tx[(address, rolling_code)] = time.monotonic()
                return await self.async_step_pair_result()
            except HomeAssistantError as err:
                _LOGGER.warning("Pairing transmission failed: %s", err)
                errors["base"] = "transmission_failed"

        return self.async_show_form(
            step_id="pair",
            errors=errors,
            description_placeholders={
                "address": self._pending_device[CONF_ADDRESS]
                if self._pending_device
                else "",
            },
        )

    async def async_step_pair_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm whether the motor reacted to the PROG command."""
        if user_input is not None:
            if user_input.get("confirm"):
                assert self._pending_device is not None
                self._devices.append(self._pending_device)
                self._pending_device = None
                return await self.async_step_menu()
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="pair_result",
            data_schema=vol.Schema({vol.Required("confirm", default=True): bool}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: Any,
    ) -> SomfyRTSOptionsFlow:
        """Return the options flow."""
        return SomfyRTSOptionsFlow()


class SomfyRTSOptionsFlow(OptionsFlow):
    """Manage general options and per-device parameters."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._edited_devices: list[dict[str, Any]] | None = None
        self._edit_index: int | None = None
        self._add_remote_index: int | None = None
        self._pending_device: dict[str, Any] | None = None
        self._remove_index: int | None = None
        self._remove_remote_index: int | None = None
        self._group_index: int | None = None
        self._member_queue: list[tuple[int, bool]] = []
        self._member_pos: int = 0
        self._group_delete_pending: bool = False
        self._unpair_targets: list[dict[str, str]] = []

    def _devices(self) -> list[dict[str, Any]]:
        """Devices, with pending edits applied."""
        if self._edited_devices is not None:
            return self._edited_devices
        return list(self.config_entry.data.get(CONF_DEVICES, []))

    def _apply_edits(self) -> None:
        """Persist pending device edits into the config entry."""
        if self._edited_devices is None:
            return
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DEVICES: self._edited_devices}
        )
        self._edited_devices = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "general",
                "transmitter",
                "add_device",
                "edit_device",
                "remove_device",
                "add_group",
                "manage_group",
                "remove_group",
                "add_remote",
                "remove_remote",
                "done",
            ],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """General settings (RX handling)."""
        if user_input is not None:
            self._apply_edits()
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_ENABLE_RX,
            self.config_entry.data.get(CONF_ENABLE_RX, DEFAULT_ENABLE_RX),
        )
        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {vol.Optional(CONF_ENABLE_RX, default=current): bool}
            ),
        )

    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the device to modify."""
        devices = self._devices()
        if user_input is not None:
            self._edit_index = int(user_input[CONF_DEVICE_INDEX])
            return await self.async_step_edit_device_params()
        if not devices:
            return await self.async_step_init()
        options = _device_select_options(devices)
        return self.async_show_form(
            step_id="edit_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_INDEX): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    def _edit_device_schema(self, device: dict[str, Any]) -> vol.Schema:
        """Build the edit form for a device, grouped into sections."""
        if device.get(CONF_GROUP):
            # A group is a grouping, not a device: name only.
            return vol.Schema(
                {
                    vol.Required(CONF_NAME, default=device.get(CONF_NAME, "")): str,
                }
            )
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_OPEN_TIME_MS,
                default=int(device.get(CONF_OPEN_TIME_MS, DEFAULT_OPEN_TIME_MS)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=600000, step=500, unit_of_measurement="ms"
                )
            ),
            vol.Required(
                CONF_CLOSE_TIME_MS,
                default=int(device.get(CONF_CLOSE_TIME_MS, DEFAULT_CLOSE_TIME_MS)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=600000, step=500, unit_of_measurement="ms"
                )
            ),
        }
        fields.update(_device_option_fields(device))
        fields[
            vol.Optional(
                CONF_LINKED_REMOTE_ADDRESSES,
                default=[
                    str(a) for a in device.get(CONF_LINKED_REMOTE_ADDRESSES, [])
                ],
            )
        ] = selector.TextSelector(
            selector.TextSelectorConfig(multiple=True)
        )
        return _sectioned_schema(
            fields,
            leading={
                vol.Required(CONF_NAME, default=device.get(CONF_NAME, "")): str,
            },
        )

    async def async_step_edit_device_params(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the parameters of the selected device."""
        assert self._edit_index is not None
        device = self._devices()[self._edit_index]
        if user_input is not None:
            user_input = _merge_section_values(user_input)
            if device.get(CONF_GROUP):
                # A group is a grouping: only its name edits.
                devices = [dict(d) for d in self._devices()]
                updated = dict(devices[self._edit_index])
                updated[CONF_NAME] = user_input[CONF_NAME]
                devices[self._edit_index] = updated
                self._edited_devices = devices
                # Persisted right away: the edit is complete.
                self._apply_edits()
                return await self.async_step_edit_device()
            remotes = list(
                dict.fromkeys(
                    str(a).strip()
                    for a in (user_input.get(CONF_LINKED_REMOTE_ADDRESSES) or [])
                    if str(a).strip()
                )
            )
            if any(
                not address_manager.is_address_valid(address) for address in remotes
            ):
                return self.async_show_form(
                    step_id="edit_device_params",
                    data_schema=self._edit_device_schema(device),
                    errors={CONF_LINKED_REMOTE_ADDRESSES: "invalid_address"},
                    description_placeholders={
                        "name": device.get(CONF_NAME, ""),
                    },
                )
            devices = [dict(d) for d in self._devices()]
            updated = dict(devices[self._edit_index])
            updated[CONF_NAME] = user_input[CONF_NAME]
            updated[CONF_OPEN_TIME_MS] = user_input[CONF_OPEN_TIME_MS]
            updated[CONF_CLOSE_TIME_MS] = user_input[CONF_CLOSE_TIME_MS]
            updated[CONF_LINKED_REMOTE_ADDRESSES] = remotes
            devices[self._edit_index] = _finalize_device_options(
                updated, user_input
            )
            self._edited_devices = devices
            # Persisted right away: the edit is complete.
            self._apply_edits()
            return await self.async_step_edit_device()

        return self.async_show_form(
            step_id="edit_device_params",
            data_schema=self._edit_device_schema(device),
            description_placeholders={
                "name": device.get(CONF_NAME, ""),
            },
        )

    async def async_step_add_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the device the detected remotes will be linked to."""
        devices = self._devices()
        if user_input is not None:
            self._add_remote_index = int(user_input[CONF_DEVICE_INDEX])
            return await self.async_step_add_remote_capture()
        if not devices:
            return await self.async_step_init()
        options = _device_select_options(devices)
        return self.async_show_form(
            step_id="add_remote",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_INDEX): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_add_remote_capture(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Detect remotes pressed while the form is displayed."""
        handler = self.hass.data.get(DOMAIN, {}).get("rx_handler")
        recent = getattr(handler, "recent_addresses", None)
        if not callable(recent):
            return self.async_show_form(
                step_id="add_remote_capture",
                data_schema=vol.Schema({}),
                errors={"base": "rx_disabled"},
            )

        candidates = recent()
        assert self._add_remote_index is not None
        linked = {
            str(a)
            for a in self._devices()[self._add_remote_index].get(
                CONF_LINKED_REMOTE_ADDRESSES, []
            )
        }
        selectable: list[SelectOptionDict] = [
            {
                "value": f"{item['address']:08d}",
                "label": (
                    f"{item['address']:08d} - {item['command']} "
                    f"({int(item['age'])} s ago)"
                ),
            }
            for item in candidates
            if f"{item['address']:08d}" not in linked
        ]

        if user_input is not None and user_input.get(CONF_LINKED_REMOTE_ADDRESSES):
            devices = [dict(d) for d in self._devices()]
            assert self._add_remote_index is not None
            device = devices[self._add_remote_index]
            addresses = [str(a) for a in device.get(CONF_LINKED_REMOTE_ADDRESSES, [])]
            for address in user_input[CONF_LINKED_REMOTE_ADDRESSES]:
                if address not in addresses:
                    addresses.append(address)
            device[CONF_LINKED_REMOTE_ADDRESSES] = addresses
            self._edited_devices = devices
            # Persisted right away: the remote is linked.
            self._apply_edits()
            return await self.async_step_init()

        # Submitting without a selection refreshes the candidate list.
        return self.async_show_form(
            step_id="add_remote_capture",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_LINKED_REMOTE_ADDRESSES, default=[]
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=selectable,
                            mode=selector.SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
            description_placeholders={"count": str(len(selectable))},
        )

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a new device to add to this entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = str(user_input.get(CONF_ADDRESS) or "").strip()
            if not address:
                address = address_manager.generate_address(self.hass)
            if not address_manager.is_address_valid(address):
                errors[CONF_ADDRESS] = "invalid_address"
            elif address_manager.is_address_used(
                self.hass, address
            ) or any(d[CONF_ADDRESS] == address for d in self._devices()):
                errors[CONF_ADDRESS] = "address_in_use"
            else:
                self._pending_device = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_ADDRESS: address,
                    CONF_DEVICE_TYPE: user_input[CONF_DEVICE_TYPE],
                    CONF_TRANSMITTER_ID: self.config_entry.data.get(
                        CONF_TRANSMITTER_ID
                    ),
                    CONF_PROTOCOL: user_input[CONF_PROTOCOL],
                    CONF_OPEN_TIME_MS: user_input[CONF_OPEN_TIME_MS],
                    CONF_CLOSE_TIME_MS: user_input[CONF_CLOSE_TIME_MS],
                }
                return await self.async_step_device_options()

        return self.async_show_form(
            step_id="add_device",
            data_schema=step_add_device_schema(self.hass),
            errors=errors,
            description_placeholders={
                "address_hint": address_manager.generate_address(self.hass),
            },
        )

    async def async_step_add_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create an RTS group and pick its members right away."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = str(user_input.get(CONF_ADDRESS) or "").strip()
            if not address:
                address = address_manager.generate_address(self.hass, group=True)
            if not address_manager.is_address_valid(address, group=True):
                errors[CONF_ADDRESS] = "invalid_address"
            elif address_manager.is_address_used(
                self.hass, address
            ) or any(d[CONF_ADDRESS] == address for d in self._devices()):
                errors[CONF_ADDRESS] = "address_in_use"
            else:
                # Lean config: no travel times, no per-type options.
                devices = [dict(d) for d in self._devices()]
                devices.append(
                    {
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_ADDRESS: address,
                        CONF_DEVICE_TYPE: user_input[CONF_DEVICE_TYPE],
                        CONF_TRANSMITTER_ID: self.config_entry.data.get(
                            CONF_TRANSMITTER_ID
                        ),
                        CONF_PROTOCOL: SomfyProtocol.RTS_56BIT.value,
                        CONF_GROUP: True,
                    }
                )
                self._edited_devices = devices
                # Persisted right away so the member wizard sees
                # the new group; it then pairs it to its covers.
                self._apply_edits()
                self._group_index = len(devices) - 1
                return await self.async_step_manage_group_members()
        return self.async_show_form(
            step_id="add_group",
            data_schema=step_add_group_schema(self.hass),
            errors=errors,
            description_placeholders={
                "address_hint": address_manager.generate_address(
                    self.hass, group=True
                ),
            },
        )

    async def async_step_device_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the parameters specific to the type and protocol."""
        assert self._pending_device is not None
        if user_input is not None:
            self._pending_device = _finalize_device_options(
                self._pending_device, _merge_section_values(user_input)
            )
            return await self.async_step_pair()

        assert self._pending_device is not None
        fields = _device_option_fields(self._pending_device)
        if not fields:
            # Nothing specific to this combination: pair right away.
            self._pending_device = _finalize_device_options(
                self._pending_device, {}
            )
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="device_options",
            data_schema=_sectioned_schema(fields),
        )

    async def _async_send_prog(self, device: dict[str, Any]) -> None:
        """Send the PROG frame for a device address (pair/unpair toggle)."""
        await self._async_send_prog_press(device, DEFAULT_FRAME_REPEATS)

    async def _async_send_prog_press(
        self, device: dict[str, Any], repeats: int
    ) -> None:
        """Send a PROG press of the given length on a device address."""
        await async_send_prog_press(
            self.hass,
            self.config_entry.data.get(CONF_TRANSMITTER_ID) or "",
            device,
            repeats,
        )

    async def _async_hold_prog_press(self, device: dict[str, Any]) -> None:
        """Send the long PROG press and let it finish on air."""
        await async_hold_prog_press(
            self.hass,
            self.config_entry.data.get(CONF_TRANSMITTER_ID) or "",
            device,
        )

    def _unpair_target_device(self, device: dict[str, Any]) -> dict[str, Any]:
        """The pseudo-device the current unpair PROG press targets.

        Group addresses are unparsed first, while the device's own
        channel is still paired and can open the motor's memory; the
        device's own address goes last.
        """
        target = self._unpair_targets[0] if self._unpair_targets else None
        return {
            CONF_ADDRESS: (
                target["address"] if target else str(device[CONF_ADDRESS])
            ),
            CONF_PROTOCOL: device.get(CONF_PROTOCOL, SomfyProtocol.RTS_56BIT.value),
        }

    def _unpair_target_label(self, device: dict[str, Any]) -> str:
        """The user-facing name of the address being unparsed."""
        target = self._unpair_targets[0] if self._unpair_targets else None
        return target["label"] if target else str(device.get(CONF_NAME, ""))

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send the PROG command while the motor is in programming mode."""
        errors: dict[str, str] = {}
        if user_input is not None and self._pending_device is not None:
            try:
                await self._async_send_prog(self._pending_device)
                return await self.async_step_pair_result()
            except HomeAssistantError as err:
                _LOGGER.warning("Pairing transmission failed: %s", err)
                errors["base"] = "transmission_failed"

        return self.async_show_form(
            step_id="pair",
            errors=errors,
            description_placeholders={
                "address": self._pending_device[CONF_ADDRESS]
                if self._pending_device
                else "",
            },
        )

    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the device to remove from the integration."""
        devices = self._devices()
        if user_input is not None:
            self._remove_index = int(user_input[CONF_DEVICE_INDEX])
            return await self.async_step_remove_device_confirm()
        if not devices:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="remove_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_INDEX): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_device_select_options(devices),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_remove_device_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the removal, optionally unpairing the motor."""
        devices = self._devices()
        if self._remove_index is None or self._remove_index >= len(devices):
            return await self.async_step_remove_device()
        assert self._remove_index is not None
        device = devices[self._remove_index]
        placeholders = {
            "name": device.get(CONF_NAME, ""),
            "address": device.get(CONF_ADDRESS, ""),
        }
        schema = vol.Schema(
            {
                vol.Optional("unpair", default=True): bool,
            }
        )
        if user_input is not None:
            # Submitting the form is the confirmation: the device was
            # already picked on the previous step.
            if user_input.get("unpair"):
                # Guided unpairing, like the Somfy procedure: open
                # the motor's memory (long PROG press), then a short
                # PROG press toggles the pairing off. The group
                # addresses the device belongs to are unparsed
                # first, while the device's own channel is still
                # paired and can open the motor's memory.
                by_address = {str(d.get(CONF_ADDRESS)): d for d in devices}
                self._unpair_targets = [
                    {
                        "address": address,
                        "label": (
                            by_address[address].get(CONF_NAME, address)
                            if address in by_address
                            else address
                        ),
                    }
                    for address in (
                        str(a) for a in device.get(CONF_GROUP_ADDRESSES, [])
                    )
                ]
                self._unpair_targets.append(
                    {
                        "address": str(device[CONF_ADDRESS]),
                        "label": str(device.get(CONF_NAME, "")),
                    }
                )
                return await self.async_step_remove_device_memory(None)
            self._unpair_targets = []
            devices = [dict(d) for d in devices]
            assert self._remove_index is not None
            del devices[self._remove_index]
            self._edited_devices = devices
            # Persisted right away: the removal is destructive.
            self._apply_edits()
            self._remove_index = None
            return await self.async_step_init()
        return self.async_show_form(
            step_id="remove_device_confirm",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    async def async_step_remove_device_memory(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open the motor's memory and confirm the jog."""
        devices = self._devices()
        if self._remove_index is None or self._remove_index >= len(devices):
            return await self.async_step_remove_device()
        device = devices[self._remove_index]
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("jogged"):
                try:
                    await self._async_send_prog(self._unpair_target_device(device))
                except HomeAssistantError as err:
                    _LOGGER.warning("Unpair transmission failed: %s", err)
                    errors["base"] = "transmission_failed"
                if not errors:
                    return await self.async_step_remove_device_unpair(None)
            else:
                errors["base"] = "memory_not_opened"
        if errors.get("base") != "transmission_failed":
            # (Re)send the long PROG press that opens the motor memory,
            # held until the burst is done on air: the unpair press sent
            # on the next validation must not queue behind it.
            try:
                await self._async_hold_prog_press(device)
            except HomeAssistantError as err:
                _LOGGER.warning("Unpair transmission failed: %s", err)
                errors["base"] = "transmission_failed"
        return self.async_show_form(
            step_id="remove_device_memory",
            data_schema=vol.Schema({vol.Required("jogged"): bool}),
            errors=errors,
            description_placeholders={
                "name": device.get(CONF_NAME, ""),
                "target": self._unpair_target_label(device),
            },
        )

    async def async_step_remove_device_unpair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the unpair jog, then remove the device."""
        devices = self._devices()
        if self._remove_index is None or self._remove_index >= len(devices):
            return await self.async_step_remove_device()
        device = devices[self._remove_index]
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("unpaired"):
                if self._unpair_targets:
                    self._unpair_targets.pop(0)
                if self._unpair_targets:
                    # More addresses to unpair: same guided sequence.
                    return await self.async_step_remove_device_memory(None)
                devices = [dict(d) for d in devices]
                assert self._remove_index is not None
                del devices[self._remove_index]
                self._edited_devices = devices
                # Persisted right away: the removal is destructive.
                self._apply_edits()
                self._remove_index = None
                return await self.async_step_init()
            errors["base"] = "unpair_not_confirmed"
            # Resend the unpair PROG press for a new confirmation.
            try:
                await self._async_send_prog(self._unpair_target_device(device))
            except HomeAssistantError as err:
                _LOGGER.warning("Unpair transmission failed: %s", err)
                errors["base"] = "transmission_failed"
        return self.async_show_form(
            step_id="remove_device_unpair",
            data_schema=vol.Schema({vol.Required("unpaired"): bool}),
            errors=errors,
            description_placeholders={
                "name": device.get(CONF_NAME, ""),
                "target": self._unpair_target_label(device),
            },
        )

    async def async_step_remove_remote(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the device from which to unlink a remote."""
        devices = self._devices()
        if user_input is not None:
            self._remove_remote_index = int(user_input[CONF_DEVICE_INDEX])
            return await self.async_step_remove_remote_confirm()
        if not devices:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="remove_remote",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_INDEX): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_device_select_options(devices),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_remove_remote_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the remote address to unlink from the device."""
        devices = self._devices()
        if (
            self._remove_remote_index is None
            or self._remove_remote_index >= len(devices)
        ):
            return await self.async_step_remove_remote()
        assert self._remove_remote_index is not None
        device = devices[self._remove_remote_index]
        remotes = [
            str(a) for a in device.get(CONF_LINKED_REMOTE_ADDRESSES, [])
        ]
        if not remotes:
            self._remove_remote_index = None
            return await self.async_step_init()
        if user_input is not None:
            address = str(user_input.get("remote_address", "")).strip()
            devices = [dict(d) for d in devices]
            assert self._remove_remote_index is not None
            devices[self._remove_remote_index] = {
                **devices[self._remove_remote_index],
                CONF_LINKED_REMOTE_ADDRESSES: [
                    a for a in remotes if a != address
                ],
            }
            self._edited_devices = devices
            # Persisted right away: the remote is detached.
            self._apply_edits()
            self._remove_remote_index = None
            return await self.async_step_init()
        return self.async_show_form(
            step_id="remove_remote_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("remote_address"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=a, label=a) for a in remotes
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            description_placeholders={
                "name": device.get(CONF_NAME, ""),
            },
        )

    async def async_step_manage_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the RTS group whose members are managed."""
        devices = self._devices()
        if user_input is not None:
            self._group_index = int(user_input[CONF_DEVICE_INDEX])
            return await self.async_step_manage_group_members()
        group_options: list[SelectOptionDict] = [
            SelectOptionDict(
                value=str(i),
                label=f"{d.get(CONF_NAME, '?')} ({d.get(CONF_ADDRESS, '?')})",
            )
            for i, d in enumerate(devices)
            if d.get(CONF_GROUP)
        ]
        if not group_options:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="manage_group",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_INDEX): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=group_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_manage_group_members(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the covers that belong to the group."""
        assert self._group_index is not None
        devices = self._devices()
        group = devices[self._group_index]
        group_address = str(group[CONF_ADDRESS])
        options, current = group_member_options(group_address, devices)
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = {str(v) for v in user_input.get(CONF_DEVICE_INDEX, [])}
            to_add = sorted(int(v) for v in selected - set(current))
            to_remove = sorted(int(v) for v in set(current) - selected)
            queue = [(index, True) for index in to_add]
            queue += [(index, False) for index in to_remove]
            if not queue:
                # Nothing to change: the pending edits (if any) stay.
                self._group_index = None
                return await self.async_step_init()
            # One member at a time: each motor's memory must be open
            # (long PROG press) and the user confirms the jogs, like
            # the manual Somfy procedure.
            self._member_queue = queue
            self._member_pos = 0
            return await self.async_step_member_memory(None)
        return self.async_show_form(
            step_id="manage_group_members",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_INDEX, default=current
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "group": group.get(CONF_NAME, ""),
            },
        )

    async def async_step_member_memory(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open the member motor's memory and confirm the jog."""
        assert self._group_index is not None
        devices = self._devices()
        group = devices[self._group_index]
        index, _add = self._member_queue[self._member_pos]
        member = devices[index]
        transmitter = self.config_entry.data.get(CONF_TRANSMITTER_ID) or ""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("jogged"):
                try:
                    await async_send_prog_press(
                        self.hass, transmitter, group, DEFAULT_FRAME_REPEATS
                    )
                except HomeAssistantError as err:
                    _LOGGER.warning("Group PROG transmission failed: %s", err)
                    errors["base"] = "transmission_failed"
                if not errors:
                    return await self.async_step_member_pair_confirm(None)
            else:
                errors["base"] = "memory_not_opened"
        if errors.get("base") != "transmission_failed":
            # (Re)send the long PROG press that opens the motor memory,
            # held until the burst is done on air: the gateway queues
            # behind it, and the pairing press must not wait it out.
            try:
                await async_hold_prog_press(self.hass, transmitter, member)
            except HomeAssistantError as err:
                _LOGGER.warning("Group PROG transmission failed: %s", err)
                errors["base"] = "transmission_failed"
        return self.async_show_form(
            step_id="member_memory",
            data_schema=vol.Schema({vol.Required("jogged"): bool}),
            errors=errors,
            description_placeholders={
                "cover": member.get(CONF_NAME, ""),
                "group": group.get(CONF_NAME, ""),
            },
        )

    async def async_step_member_pair_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the group PROG press jogged the motor."""
        assert self._group_index is not None
        devices = self._devices()
        group = devices[self._group_index]
        group_address = str(group[CONF_ADDRESS])
        index, add = self._member_queue[self._member_pos]
        member = devices[index]
        transmitter = self.config_entry.data.get(CONF_TRANSMITTER_ID) or ""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("paired"):
                devices = [dict(d) for d in devices]
                member = devices[index]
                addresses = [
                    str(a) for a in member.get(CONF_GROUP_ADDRESSES, [])
                ]
                if add:
                    addresses.append(group_address)
                else:
                    addresses.remove(group_address)
                member[CONF_GROUP_ADDRESSES] = addresses
                self._edited_devices = devices
                self._member_pos += 1
                # Persisted right away: a wizard abandoned mid-queue
                # would leave the config out of sync with the motors,
                # and a later run would re-pair (that is, toggle off)
                # covers that are already group members.
                self._apply_edits()
                if self._member_pos < len(self._member_queue):
                    return await self.async_step_member_memory(None)
                # Refresh the live entities before the group deletion
                # below shifts the device indices.
                self._refresh_membership_entities(
                    devices,
                    [i for i, _ in self._member_queue],
                    group_address,
                )
                if self._group_delete_pending:
                    # The members forgot the address: drop the group.
                    del devices[self._group_index]
                    self._group_delete_pending = False
                    self._edited_devices = devices
                    # Persisted right away: the removal is destructive.
                    self._apply_edits()
                self._member_queue = []
                self._member_pos = 0
                self._group_index = None
                return await self.async_step_init()
            errors["base"] = "pair_not_confirmed"
            # Resend the group PROG press for a new confirmation.
            try:
                await async_send_prog_press(
                    self.hass, transmitter, group, DEFAULT_FRAME_REPEATS
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Group PROG transmission failed: %s", err)
                errors["base"] = "transmission_failed"
        return self.async_show_form(
            step_id="member_pair_confirm",
            data_schema=vol.Schema({vol.Required("paired"): bool}),
            errors=errors,
            description_placeholders={
                "cover": member.get(CONF_NAME, ""),
                "group": group.get(CONF_NAME, ""),
            },
        )

    async def async_step_remove_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the RTS group to remove."""
        devices = self._devices()
        if user_input is not None:
            self._group_index = int(user_input[CONF_DEVICE_INDEX])
            return await self.async_step_remove_group_confirm()
        group_options: list[SelectOptionDict] = [
            SelectOptionDict(
                value=str(i),
                label=f"{d.get(CONF_NAME, '?')} ({d.get(CONF_ADDRESS, '?')})",
            )
            for i, d in enumerate(devices)
            if d.get(CONF_GROUP)
        ]
        if not group_options:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="remove_group",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_INDEX): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=group_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_remove_group_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the group removal, optionally unpairing the members."""
        assert self._group_index is not None
        devices = self._devices()
        group = devices[self._group_index]
        group_address = str(group[CONF_ADDRESS])
        _, current = group_member_options(group_address, devices)
        placeholders = {
            "group": group.get(CONF_NAME, ""),
            "count": str(len(current)),
        }
        schema = vol.Schema(
            {
                vol.Required("confirm", default=False): bool,
                vol.Optional("unpair", default=True): bool,
            }
        )
        if user_input is not None:
            if not user_input.get("confirm"):
                self._group_index = None
                return await self.async_step_remove_group()
            if user_input.get("unpair") and current:
                # Guided unpairing of every member; the group is dropped
                # once the last motor has confirmed its jog.
                self._group_delete_pending = True
                self._member_queue = [(int(v), False) for v in current]
                self._member_pos = 0
                return await self.async_step_member_memory(None)
            devices = [dict(d) for d in devices]
            del devices[self._group_index]
            # Persisted right away: the removal is destructive.
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_DEVICES: devices},
            )
            self._edited_devices = devices
            self._group_index = None
            return await self.async_step_init()
        return self.async_show_form(
            step_id="remove_group_confirm",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    def _refresh_membership_entities(
        self,
        devices: list[dict[str, Any]],
        changed_indices: list[int],
        group_address: str,
    ) -> None:
        """Push the new membership to the live entities, if loaded."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        entities = getattr(runtime, "entities", None)
        if not entities:
            return
        by_address = {
            str(getattr(e, "_address", "")): e for e in entities.values()
        }
        for index in changed_indices:
            member = by_address.get(str(devices[index][CONF_ADDRESS]))
            if member is not None:
                member.async_update_group_addresses(
                    [
                        str(a)
                        for a in devices[index].get(CONF_GROUP_ADDRESSES, [])
                    ]
                )
        group_entity = by_address.get(group_address)
        if group_entity is not None:
            group_entity.async_write_ha_state()

    async def async_step_pair_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm whether the motor reacted to the PROG command."""
        if user_input is not None:
            if user_input.get("confirm"):
                devices = [dict(d) for d in self._devices()]
                assert self._pending_device is not None
                devices.append(dict(self._pending_device))
                self._edited_devices = devices
                # Persisted right away: the device is paired.
                self._apply_edits()
                self._pending_device = None
                return await self.async_step_init()
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="pair_result",
            data_schema=vol.Schema({vol.Required("confirm", default=True): bool}),
        )

    async def async_step_transmitter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the RF transmitter entity used by the entry.

        Repair path: the entry stores the transmitter entity id, which
        changes if the ESPHome device or entity is renamed.
        """
        current = self.config_entry.data.get(CONF_TRANSMITTER_ID, "")
        if user_input is not None:
            self._apply_edits()
            entry = self.config_entry
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_TRANSMITTER_ID: user_input[CONF_TRANSMITTER_ID],
                },
            )
            return self.async_create_entry(title="", data=dict(entry.options or {}))

        transmitters = rf.get_transmitters(self.hass)
        if not transmitters:
            return self.async_show_form(
                step_id="transmitter",
                data_schema=vol.Schema({}),
                errors={"base": "no_transmitters"},
            )
        options = [SelectOptionDict(value=t, label=t) for t in transmitters]
        # The stored transmitter may no longer exist (renamed gateway):
        # default to the first available one instead of an invalid id.
        default = current if current in transmitters else transmitters[0]
        return self.async_show_form(
            step_id="transmitter",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TRANSMITTER_ID, default=default
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Apply pending edits and close the options flow."""
        self._apply_edits()
        return self.async_create_entry(
            title="", data=dict(self.config_entry.options)
        )
