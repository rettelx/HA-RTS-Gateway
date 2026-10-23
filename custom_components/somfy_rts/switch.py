"""Switch platform for Somfy RTS on/off plug receivers.

Somfy RTS plug receivers (e.g., Prise télécommandée On/Off 3600 W RTS,
refs 2401091 / 2401093 / 2401363) and RTS relay receivers toggle their
output when they hear the toggle button of a paired remote. The toggle
button varies between receiver models, so the RTS command used to switch
(My, Up or Down) is configurable per device.

These receivers give no feedback, so the state is assumed and corrected
when a linked physical remote sends the toggle command over the air.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import rf
from .availability import TransmitterAvailabilityMixin
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_TYPE,
    CONF_LINKED_REMOTE_ADDRESSES,
    CONF_NAME,
    CONF_PROTOCOL,
    CONF_TOGGLE_COMMAND,
    CONF_TRANSMITTER_ID,
    DEVICE_TYPE_MAPPING,
    DOMAIN,
    SomfyCommand,
    SomfyDeviceType,
    SomfyProtocol,
)
from .rolling_code import RollingCodeStore

# Radio actions are serialized per entity to avoid overlapping bursts.
PARALLEL_UPDATES = 1
_LOGGER = logging.getLogger(__name__)

_TOGGLE_COMMANDS = {
    SomfyCommand.MY.value: SomfyCommand.MY,
    SomfyCommand.UP.value: SomfyCommand.UP,
    SomfyCommand.DOWN.value: SomfyCommand.DOWN,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Somfy RTS switches from a config entry."""
    devices = entry.data.get("devices", [])
    entities = [
        SomfyRTSSwitch(hass, entry, device)
        for device in devices
        if SomfyDeviceType(device.get(CONF_DEVICE_TYPE)) == SomfyDeviceType.SOCKET
    ]
    async_add_entities(entities)


class SomfyRTSSwitch(TransmitterAvailabilityMixin, SwitchEntity, RestoreEntity):
    """Representation of an RTS on/off plug receiver."""

    _attr_has_entity_name = True
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_assumed_state = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device_config: dict[str, Any],
    ) -> None:
        """Initialize the switch."""
        self._hass = hass
        self._entry = entry
        self._device_config = device_config
        self._store: RollingCodeStore = hass.data[DOMAIN]["rolling_codes"]

        self._address = str(device_config[CONF_ADDRESS])
        self._address_int = int(self._address)
        self._transmitter_id = device_config[CONF_TRANSMITTER_ID]
        self._protocol = SomfyProtocol(
            device_config.get(CONF_PROTOCOL, SomfyProtocol.RTS_56BIT)
        )
        self._toggle_command = _TOGGLE_COMMANDS.get(
            device_config.get(CONF_TOGGLE_COMMAND, SomfyCommand.MY.value),
            SomfyCommand.MY,
        )
        self._linked_remote_addresses = [
            str(a) for a in device_config.get(CONF_LINKED_REMOTE_ADDRESSES, [])
        ]

        self._attr_unique_id = f"{DOMAIN}_{self._address}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=device_config.get(CONF_NAME, f"Somfy RTS {self._address}"),
            manufacturer="Somfy",
            model=SomfyDeviceType.SOCKET.value,
            via_device=(DOMAIN, self._transmitter_id),
        )
        self._attr_icon = DEVICE_TYPE_MAPPING.get(SomfyDeviceType.SOCKET, {}).get("icon")
        self._attr_is_on = False
        self._last_rx_device: str | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes."""
        return {
            "address": self._address,
            "protocol": self._protocol.value,
            "toggle_command": self._toggle_command.value,
            "transmitter_id": self._transmitter_id,
            "linked_remote_addresses": self._linked_remote_addresses,
            "last_rx_device": self._last_rx_device,
        }

    @property
    def linked_remote_addresses_int(self) -> list[int]:
        """Return linked remote addresses as integers."""
        return [int(a) for a in self._linked_remote_addresses]

    def async_update_linked_remotes(self, addresses: list[str]) -> None:
        """Update the linked remote addresses."""
        self._linked_remote_addresses = [str(a) for a in addresses]
        self._device_config[CONF_LINKED_REMOTE_ADDRESSES] = self._linked_remote_addresses
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register the entity and restore its last state."""
        await super().async_added_to_hass()

        registry = self._entry.runtime_data.entities
        registry[self.entity_id] = self

        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the entity."""
        registry = self._entry.runtime_data.entities
        registry.pop(self.entity_id, None)
        await super().async_will_remove_from_hass()

    async def _async_send_command(self, command: SomfyCommand) -> None:
        """Send a command through the RF transmitter."""
        rolling_code = await self._store.async_get_next(self._address_int)
        await rf.async_send_command(
            self._hass,
            self._transmitter_id,
            self._address_int,
            command,
            rolling_code,
            protocol=self._protocol,
        )
        recent_tx = self._hass.data[DOMAIN].setdefault("recent_tx", {})
        recent_tx[(self._address_int, rolling_code)] = time.monotonic()
        _LOGGER.debug(
            "Sent %s to %s (rolling code %d)",
            command.value,
            self._address,
            rolling_code,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the plug on."""
        if self._attr_is_on:
            return
        await self._async_send_command(self._toggle_command)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the plug off."""
        if not self._attr_is_on:
            return
        await self._async_send_command(self._toggle_command)
        self._attr_is_on = False
        self.async_write_ha_state()

    def async_update_from_rx(
        self,
        command: SomfyCommand | None,
        rolling_code: int,
        device: str | None,
        step: int = 0,
    ) -> None:
        """Update the state when a linked remote toggles the plug."""
        self._last_rx_device = device
        if command == self._toggle_command:
            self._attr_is_on = not self._attr_is_on
            self.async_write_ha_state()
