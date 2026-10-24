"""Light platform for RTS dimmer and lighting receivers.

RTS lighting products (e.g., Somfy Prise télécommandée variateur 200 W RTS
ref 2401092, récepteur éclairage encastrable 1811251, Slim Receiver RTS
1822215) are toggled by the button of a paired RTS remote and dimmed with
the step wheel of a Modulis-type remote (80-bit Step Up/Down frames).

- On/off uses a configurable toggle command (My, Up or Down), like the
  switch platform: receiver models differ.
- Brightness requires the 80-bit protocol: `set_brightness` sends Step
  Up/Down frames spaced like a molette turn and tracks an estimated
  brightness configurable per device (`brightness_step_percent`).

These receivers give no feedback; the state is assumed and corrected when
a linked physical remote is heard over the air.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
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
    CONF_BRIGHTNESS_STEP_PERCENT,
    CONF_DEVICE_TYPE,
    CONF_LINKED_REMOTE_ADDRESSES,
    CONF_NAME,
    CONF_PROTOCOL,
    CONF_STEP_SIZE,
    CONF_TOGGLE_COMMAND,
    CONF_TRANSMITTER_ID,
    DEFAULT_BRIGHTNESS_STEP_PERCENT,
    DEFAULT_STEP_SIZE,
    DEVICE_TYPE_MAPPING,
    DOMAIN,
    TILT_STEP_INTERVAL_S,
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


def _clamp(value: int, low: int = 0, high: int = 255) -> int:
    return min(high, max(low, value))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RTS lights from a config entry."""
    devices = entry.data.get("devices", [])
    entities = [
        SomfyRTSLight(hass, entry, device)
        for device in devices
        if SomfyDeviceType(device.get(CONF_DEVICE_TYPE)) == SomfyDeviceType.LIGHT
    ]
    async_add_entities(entities)


class SomfyRTSLight(TransmitterAvailabilityMixin, LightEntity, RestoreEntity):
    """Representation of an RTS dimmer/lighting receiver."""

    _attr_has_entity_name = True
    _attr_assumed_state = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device_config: dict[str, Any],
    ) -> None:
        """Initialize the light."""
        self._hass = hass
        self._entry = entry
        self._device_config = device_config
        self._store: RollingCodeStore = hass.data[DOMAIN]["rolling_codes"]

        self._address = str(device_config[CONF_ADDRESS])
        self._address_int = int(self._address)
        self._transmitter_id = device_config[CONF_TRANSMITTER_ID]
        self._protocol = SomfyProtocol(
            device_config.get(CONF_PROTOCOL, SomfyProtocol.RTS_80BIT)
        )
        self._toggle_command = _TOGGLE_COMMANDS.get(
            device_config.get(CONF_TOGGLE_COMMAND, SomfyCommand.MY.value),
            SomfyCommand.MY,
        )
        self._brightness_step = int(
            device_config.get(CONF_BRIGHTNESS_STEP_PERCENT, DEFAULT_BRIGHTNESS_STEP_PERCENT)
        )
        self._step_size = int(device_config.get(CONF_STEP_SIZE, DEFAULT_STEP_SIZE))
        self._linked_remote_addresses = [
            str(a) for a in device_config.get(CONF_LINKED_REMOTE_ADDRESSES, [])
        ]

        self._attr_unique_id = f"{DOMAIN}_{self._address}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name=device_config.get(CONF_NAME, f"RTS {self._address}"),
            model=SomfyDeviceType.LIGHT.value,
        )
        self._attr_icon = DEVICE_TYPE_MAPPING.get(SomfyDeviceType.LIGHT, {}).get("icon")

        self._supports_brightness = self._protocol == SomfyProtocol.RTS_80BIT
        if self._supports_brightness:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

        self._attr_is_on = False
        self._brightness = 255
        self._dim_task: asyncio.Task[None] | None = None
        self._last_rx_device: str | None = None

    @property
    def brightness(self) -> int | None:
        """Return the estimated brightness (0-255)."""
        if not self._supports_brightness:
            return None
        return self._brightness

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
            brightness = last_state.attributes.get("brightness")
            if self._supports_brightness and isinstance(brightness, (int, float)):
                self._brightness = _clamp(int(brightness))

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the entity."""
        registry = self._entry.runtime_data.entities
        registry.pop(self.entity_id, None)
        self._cancel_dim_task()
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
            step_size=self._step_size,
        )
        recent_tx = self._hass.data[DOMAIN].setdefault("recent_tx", {})
        recent_tx[(self._address_int, rolling_code)] = time.monotonic()
        _LOGGER.debug(
            "Sent %s to %s (rolling code %d)",
            command.value,
            self._address,
            rolling_code,
        )

    def _cancel_dim_task(self) -> None:
        """Cancel a running dim movement."""
        if self._dim_task is not None and not self._dim_task.done():
            self._dim_task.cancel()
        self._dim_task = None

    async def _async_dim_to_target(self, target: int) -> None:
        """Send step commands until the brightness target is reached."""
        delta = target - self._brightness
        if delta == 0:
            return
        command = SomfyCommand.STEP_UP if delta > 0 else SomfyCommand.STEP_DOWN
        step = self._brightness_step * 255 // 100
        count = max(1, round(abs(delta) / step)) if step > 0 else 1
        # Cancellation keeps the last partial estimate: the snap below
        # only runs once every step frame has been sent.
        for _ in range(count):
            await self._async_send_command(command)
            self._brightness = _clamp(self._brightness + (step if delta > 0 else -step))
            self.async_write_ha_state()
            await asyncio.sleep(TILT_STEP_INTERVAL_S)
        self._brightness = _clamp(target)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally dimming to a brightness."""
        if not self._attr_is_on:
            await self._async_send_command(self._toggle_command)
            self._attr_is_on = True
        if ATTR_BRIGHTNESS in kwargs and self._supports_brightness:
            target = _clamp(int(kwargs[ATTR_BRIGHTNESS]))
            self._cancel_dim_task()
            self._dim_task = self._hass.async_create_task(
                self._async_dim_to_target(target)
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        self._cancel_dim_task()
        if self._attr_is_on:
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
        """Update the state when a linked remote controls the receiver."""
        self._last_rx_device = device
        if command == self._toggle_command:
            self._attr_is_on = not self._attr_is_on
            self.async_write_ha_state()
        elif self._supports_brightness and command in (
            SomfyCommand.STEP_UP,
            SomfyCommand.STEP_DOWN,
        ):
            step_value = self._brightness_step * 255 // 100
            self._brightness = _clamp(
                self._brightness
                + (step_value if command == SomfyCommand.STEP_UP else -step_value)
            )
            self.async_write_ha_state()
