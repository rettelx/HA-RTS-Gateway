"""Constants for the Somfy RTS integration."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from homeassistant.components.cover import CoverDeviceClass
from homeassistant.const import Platform

# =============================================================================
# DOMAIN
# =============================================================================
DOMAIN = "somfy_rts"

# =============================================================================
# RF / PROTOCOL
# =============================================================================
RTS_FREQUENCY_HZ = 433_420_000

# HA event fired by the ESPHome somfy_rts component for received RTS frames
RX_EVENT = "esphome.somfy_rts_rx"

# =============================================================================
# ENUMS
# =============================================================================
class SomfyProtocol(StrEnum):
    """Somfy RTS frame lengths.

    Most RTS motors use the 56-bit protocol. Remotes with a step wheel
    (Somfy Modulis/Situo, Venetian blind tilt) and their motors exchange
    80-bit frames.
    """

    RTS_56BIT = "rts_56bit"
    RTS_80BIT = "rts_80bit"


class SomfyDeviceType(StrEnum):
    """Types of Somfy RTS devices.

    Cover/shade types of the RTS catalog: roller, blind, draperies
    opening left/right/center, awning, shutter, 1- and 3-button garage
    doors, gates opening left/center/right. Dry-contact types (bare
    motors driven through GPIO relay modules) are intentionally
    absent: an RF gateway cannot talk to them.

    Non-cover RTS receivers from the Somfy catalog are also supported:
    on/off plug receivers ("socket") and dimmer/lighting receivers
    ("light").
    """

    ROLLER_SHUTTER = "roller_shutter"
    SHUTTER = "shutter"
    BLIND = "blind"
    DRAPERY = "drapery"
    DRAPERY_LEFT = "ldrapery"
    DRAPERY_RIGHT = "rdrapery"
    DRAPERY_CENTER = "cdrapery"
    AWNING = "awning"
    GARAGE = "garage"
    GARAGE_1BUTTON = "garage1"
    GARAGE_3BUTTON = "garage3"
    GATE = "gate"
    GATE_LEFT = "lgate"
    GATE_CENTER = "cgate"
    GATE_RIGHT = "rgate"
    GATE_LEFT_1BUTTON = "lgate1"
    GATE_CENTER_1BUTTON = "cgate1"
    GATE_RIGHT_1BUTTON = "rgate1"
    SOCKET = "socket"
    LIGHT = "light"


class SomfyCommand(StrEnum):
    """Somfy RTS commands."""

    UP = "UP"
    DOWN = "DOWN"
    MY = "MY"
    STOP = "STOP"
    PROG = "PROG"
    STEP_UP = "STEP_UP"
    STEP_DOWN = "STEP_DOWN"
    SENSOR = "SENSOR"


# How the cover is stopped at an intermediate position:
# - "my": the MY button (default, works on motors with MY support)
# - "pulse": a brief opposite-direction press (motors without MY)
STOP_METHOD_MY = "my"
STOP_METHOD_PULSE = "pulse"

# =============================================================================
# CONFIGURATION KEYS
# =============================================================================
CONF_NAME = "name"
CONF_ADDRESS = "address"
CONF_DEVICE_TYPE = "device_type"
CONF_TRANSMITTER_ID = "transmitter_id"
CONF_OPEN_TIME_MS = "open_time_ms"
CONF_CLOSE_TIME_MS = "close_time_ms"
CONF_ENABLE_RX = "enable_rx"
CONF_GROUP = "is_group"
CONF_INVERT = "invert"
CONF_INVERT_POSITION = "invert_position"
CONF_LINKED_REMOTE_ADDRESSES = "linked_remote_addresses"
CONF_DEVICES = "devices"
CONF_PROTOCOL = "protocol"
CONF_TILT = "tilt"
CONF_TILT_STEP_PERCENT = "tilt_step_percent"
CONF_TILT_TIME_MS = "tilt_time_ms"
CONF_STEP_SIZE = "step_size"
CONF_SENSORS = "sensors"
CONF_DEVICE_INDEX = "device_index"
CONF_MY_POSITION = "my_position"
CONF_TOGGLE_COMMAND = "toggle_command"
CONF_BRIGHTNESS_STEP_PERCENT = "brightness_step_percent"
CONF_STOP_METHOD = "stop_method"
CONF_SIMULATE_MY = "simulate_my"
CONF_GROUP_ADDRESSES = "group_addresses"

# =============================================================================
# DEFAULTS
# =============================================================================
DEFAULT_OPEN_TIME_MS = 20000
DEFAULT_CLOSE_TIME_MS = 20000
DEFAULT_ENABLE_RX = True
DEFAULT_FRAME_REPEATS = 3
# 80-bit step wheel defaults
DEFAULT_STEP_SIZE = 100
# Full tilt travel time for a timed tilt motor
DEFAULT_TILT_TIME_MS = 7000
DEFAULT_TILT_STEP_PERCENT = 7
# Long MY press that memorizes the current position on native motors
SETMY_REPEATS = 35
# Long PROG press that puts the motor in programming mode (the same
# frame repeated on air, ~2-5 s hold like SETMY_REPEATS)
PROG_MODE_REPEATS = 35
# Delay between step frames of a tilt movement, in seconds
TILT_STEP_INTERVAL_S = 0.75
# Long-press detection on RTS remotes: a
# tilt motor reads a long press as tilt and a short press as lift. A
# press held down repeats the same frame on air; this many repeats or
# more mark a long press.
TILT_PRESS_REPEATS = 15
# Silence after the last repeated frame before a press is resolved.
TILT_PRESS_END_S = 0.5
# Delay after the long PROG press for the motor to jog and enter
# its programming mode before the group PROG is sent.
PROG_MODE_JOG_S = 2.0
# Delay after a group PROG press for the motor to jog and leave its
# programming mode before the next member is processed.
PROG_PAIR_SETTLE_S = 1.0

# RTS sun/wind sensor frames (Somfy Sunis, Eolis, Soliris): the command
# nibble is Sensor (0xE) and the 16-bit field normally carrying the rolling
# code holds the sensor state (bit 0 = wind, bit 1 = sun).
SENSOR_WIND_BIT = 0x01
SENSOR_SUN_BIT = 0x02

# Default step for light dimming over the Modulis wheel (percent per frame)
DEFAULT_BRIGHTNESS_STEP_PERCENT = 5

# Device types routed to each platform
TILT_DEVICE_TYPES = frozenset({SomfyDeviceType.BLIND})
# Device types without distinct Up/Down commands: flipping them is
# meaningless.
FLIP_COMMAND_EXCLUDED_DEVICE_TYPES = frozenset({SomfyDeviceType.GARAGE_1BUTTON})

COVER_DEVICE_TYPES = frozenset(
    {
        SomfyDeviceType.ROLLER_SHUTTER,
        SomfyDeviceType.SHUTTER,
        SomfyDeviceType.BLIND,
        SomfyDeviceType.DRAPERY,
        SomfyDeviceType.DRAPERY_LEFT,
        SomfyDeviceType.DRAPERY_RIGHT,
        SomfyDeviceType.DRAPERY_CENTER,
        SomfyDeviceType.AWNING,
        SomfyDeviceType.GARAGE,
        SomfyDeviceType.GARAGE_1BUTTON,
        SomfyDeviceType.GARAGE_3BUTTON,
        SomfyDeviceType.GATE,
        SomfyDeviceType.GATE_LEFT,
        SomfyDeviceType.GATE_CENTER,
        SomfyDeviceType.GATE_RIGHT,
        SomfyDeviceType.GATE_LEFT_1BUTTON,
        SomfyDeviceType.GATE_CENTER_1BUTTON,
        SomfyDeviceType.GATE_RIGHT_1BUTTON,
    }
)

# =============================================================================
# USER-FACING DEVICE TYPE NAMES
# =============================================================================
# Localized names shown in the wizard dropdowns and as the device
# model. Unknown languages fall back to English.
DEVICE_TYPE_LABELS: dict[SomfyDeviceType, dict[str, str]] = {
    SomfyDeviceType.ROLLER_SHUTTER: {"en": "Roller shutter", "fr": "Volet roulant"},
    SomfyDeviceType.SHUTTER: {"en": "Shutter", "fr": "Volet"},
    SomfyDeviceType.BLIND: {"en": "Venetian blind", "fr": "Store vénitien"},
    SomfyDeviceType.DRAPERY: {"en": "Drapery", "fr": "Rideau"},
    SomfyDeviceType.DRAPERY_LEFT: {"en": "Drapery, opens left", "fr": "Rideau, ouverture à gauche"},
    SomfyDeviceType.DRAPERY_RIGHT: {"en": "Drapery, opens right", "fr": "Rideau, ouverture à droite"},
    SomfyDeviceType.DRAPERY_CENTER: {"en": "Drapery, opens from center", "fr": "Rideau, ouverture du centre"},
    SomfyDeviceType.AWNING: {"en": "Awning", "fr": "Store banne"},
    SomfyDeviceType.GARAGE: {"en": "Garage door", "fr": "Porte de garage"},
    SomfyDeviceType.GARAGE_1BUTTON: {"en": "Garage door (1-button remote)", "fr": "Porte de garage (télécommande 1 bouton)"},
    SomfyDeviceType.GARAGE_3BUTTON: {"en": "Garage door (3-button remote)", "fr": "Porte de garage (télécommande 3 boutons)"},
    SomfyDeviceType.GATE: {"en": "Gate", "fr": "Portail"},
    SomfyDeviceType.GATE_LEFT: {"en": "Gate, opens left", "fr": "Portail, ouverture à gauche"},
    SomfyDeviceType.GATE_CENTER: {"en": "Gate, opens from center", "fr": "Portail, ouverture du centre"},
    SomfyDeviceType.GATE_RIGHT: {"en": "Gate, opens right", "fr": "Portail, ouverture à droite"},
    SomfyDeviceType.GATE_LEFT_1BUTTON: {"en": "Gate, opens left (1-button)", "fr": "Portail, ouverture à gauche (1 bouton)"},
    SomfyDeviceType.GATE_CENTER_1BUTTON: {"en": "Gate, opens from center (1-button)", "fr": "Portail, ouverture du centre (1 bouton)"},
    SomfyDeviceType.GATE_RIGHT_1BUTTON: {"en": "Gate, opens right (1-button)", "fr": "Portail, ouverture à droite (1 bouton)"},
    SomfyDeviceType.SOCKET: {"en": "On/off plug", "fr": "Prise On/Off"},
    SomfyDeviceType.LIGHT: {"en": "Dimmer/lighting receiver", "fr": "Variateur / récepteur éclairage"},
}

# Model of the sun/wind sensor devices
SENSOR_MODEL_LABELS: dict[str, str] = {
    "en": "RTS sun/wind sensor",
    "fr": "Capteur soleil/vent RTS",
}


def localized_label(hass: Any, labels: dict[str, str], fallback: str) -> str:
    """Return the label for the current HA language (English fallback)."""
    language = str(getattr(getattr(hass, "config", None), "language", None) or "en")
    return labels.get(language[:2], labels.get("en", fallback))


def device_type_label(hass: Any, device_type: SomfyDeviceType) -> str:
    """Localized name of a device type (English fallback)."""
    labels = DEVICE_TYPE_LABELS.get(device_type, {})
    return localized_label(hass, labels, device_type.value)


# =============================================================================
# PLATFORMS
# =============================================================================
PLATFORMS = [
    Platform.COVER,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.LIGHT,
    Platform.BUTTON,
]

# =============================================================================
# SERVICE NAMES
# =============================================================================
SERVICE_PAIR = "pair"
SERVICE_GOTO_MY_POSITION = "goto_my_position"
SERVICE_SET_MY_POSITION = "set_my_position"
SERVICE_SET_POSITION = "set_position"
SERVICE_SET_TILT_POSITION = "set_tilt_position"
SERVICE_RECALIBRATE = "recalibrate"
SERVICE_ENTER_PROGRAMMING_MODE = "enter_programming_mode"
SERVICE_SEND_PROG = "send_prog"
SERVICE_MEMORIZE_MY_POSITION = "memorize_my_position"
SERVICE_SEND_COMMAND = "send_command"
SERVICE_SEND_STEP_COMMAND = "send_step_command"
SERVICE_LINK_REMOTE = "link_remote"
SERVICE_UNLINK_REMOTE = "unlink_remote"
SERVICE_ADD_TO_GROUP = "add_to_group"
SERVICE_REMOVE_FROM_GROUP = "remove_from_group"

# =============================================================================
# EVENT NAMES
# =============================================================================
EVENT_PAIRING_ACTION = "somfy_rts_pairing_action"
# HA event fired for every received RTS frame (deduplicated)
EVENT_FRAME = "somfy_rts_frame"

# =============================================================================
# DEVICE TYPE MAPPING
# =============================================================================
DEVICE_TYPE_MAPPING = {
    SomfyDeviceType.ROLLER_SHUTTER: {"icon": "mdi:window-shutter"},
    SomfyDeviceType.SHUTTER: {"icon": "mdi:window-shutter"},
    SomfyDeviceType.BLIND: {"icon": "mdi:blinds"},
    SomfyDeviceType.DRAPERY: {"icon": "mdi:curtain"},
    SomfyDeviceType.DRAPERY_LEFT: {"icon": "mdi:curtain"},
    SomfyDeviceType.DRAPERY_RIGHT: {"icon": "mdi:curtain"},
    SomfyDeviceType.DRAPERY_CENTER: {"icon": "mdi:curtain"},
    SomfyDeviceType.AWNING: {"icon": "mdi:store"},
    SomfyDeviceType.GARAGE: {"icon": "mdi:garage"},
    SomfyDeviceType.GARAGE_1BUTTON: {"icon": "mdi:garage"},
    SomfyDeviceType.GARAGE_3BUTTON: {"icon": "mdi:garage"},
    SomfyDeviceType.GATE: {"icon": "mdi:gate"},
    SomfyDeviceType.GATE_LEFT: {"icon": "mdi:gate"},
    SomfyDeviceType.GATE_CENTER: {"icon": "mdi:gate"},
    SomfyDeviceType.GATE_RIGHT: {"icon": "mdi:gate"},
    SomfyDeviceType.GATE_LEFT_1BUTTON: {"icon": "mdi:gate"},
    SomfyDeviceType.GATE_CENTER_1BUTTON: {"icon": "mdi:gate"},
    SomfyDeviceType.GATE_RIGHT_1BUTTON: {"icon": "mdi:gate"},
    SomfyDeviceType.SOCKET: {"icon": "mdi:power-socket-eu"},
    SomfyDeviceType.LIGHT: {"icon": "mdi:lightbulb"},
}

DEVICE_TYPE_TO_DEVICE_CLASS = {
    SomfyDeviceType.ROLLER_SHUTTER: CoverDeviceClass.SHUTTER,
    SomfyDeviceType.SHUTTER: CoverDeviceClass.SHUTTER,
    SomfyDeviceType.BLIND: CoverDeviceClass.BLIND,
    SomfyDeviceType.DRAPERY: CoverDeviceClass.CURTAIN,
    SomfyDeviceType.DRAPERY_LEFT: CoverDeviceClass.CURTAIN,
    SomfyDeviceType.DRAPERY_RIGHT: CoverDeviceClass.CURTAIN,
    SomfyDeviceType.DRAPERY_CENTER: CoverDeviceClass.CURTAIN,
    SomfyDeviceType.AWNING: CoverDeviceClass.AWNING,
    SomfyDeviceType.GARAGE: CoverDeviceClass.GARAGE,
    SomfyDeviceType.GARAGE_1BUTTON: CoverDeviceClass.GARAGE,
    SomfyDeviceType.GARAGE_3BUTTON: CoverDeviceClass.GARAGE,
    SomfyDeviceType.GATE: CoverDeviceClass.GATE,
    SomfyDeviceType.GATE_LEFT: CoverDeviceClass.GATE,
    SomfyDeviceType.GATE_CENTER: CoverDeviceClass.GATE,
    SomfyDeviceType.GATE_RIGHT: CoverDeviceClass.GATE,
    SomfyDeviceType.GATE_LEFT_1BUTTON: CoverDeviceClass.GATE,
    SomfyDeviceType.GATE_CENTER_1BUTTON: CoverDeviceClass.GATE,
    SomfyDeviceType.GATE_RIGHT_1BUTTON: CoverDeviceClass.GATE,
}

# =============================================================================
# LIMITS
# =============================================================================
DEVICE_ADDRESS_MIN = 0
DEVICE_ADDRESS_MAX = 16777215  # 0xFFFFFF (24 bits)
GROUP_ADDRESS_MIN = 1000000
GROUP_ADDRESS_MAX = 2097151
RX_DUPLICATE_TTL = 1.5  # seconds
# Window during which frames identical to our own transmissions are ignored
# (the gateway's CC1101 hears the frames we transmit).
TX_LOOPBACK_TTL = 3.0  # seconds
