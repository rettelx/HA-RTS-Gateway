"""Somfy RTS protocol helpers.

56-bit transmission relies on the native ``SomfyRTSCommand`` from the
``rf-protocols`` library shipped with the ``radio_frequency`` integration
(full frame: checksum, obfuscation, Manchester encoding, wake-up pulse,
repeats).

80-bit frames (``RTS_80BIT``: remotes with a step wheel such as Somfy
Modulis/Situo, used for Venetian blind tilt) are not covered by
rf-protocols, so this module implements a compatible encoder:

* frame = the standard 56-bit frame plus 3 extension bytes that are NOT
  obfuscated; byte 7 acts as a repeat counter,
* an additional 4-bit checksum over the extension bytes,
* TX preamble: 12 hardware sync pulses on the first frame (after the
  wake-up pulse), 14 on repeats; inter-frame silence reduced to 15055 us
  (30415 - 24 * 640),
* RX: frames with 12/13 hardware sync runs (6 pulses, real step-wheel
  remotes) or more than 17 runs are 80-bit frames.

A decoder for raw OOK timings (Python twin of the ESPHome ``somfy_rts``
external component) handles both frame lengths and is used by tests to
validate the algorithms against encoder output.
"""

from __future__ import annotations

from dataclasses import dataclass

from rf_protocols.codes.somfy.rts import SomfyRTSButton
from rf_protocols.commands.ook import OOKCommand
from rf_protocols.commands.somfy_rts import SomfyRTSCommand

from ..const import RTS_FREQUENCY_HZ, SomfyCommand

# =============================================================================
# RTS button nibbles (control byte high nibble)
# =============================================================================
BUTTON_MY = 0x1
BUTTON_UP = 0x2
BUTTON_MY_UP = 0x3
BUTTON_DOWN = 0x4
BUTTON_MY_DOWN = 0x5
BUTTON_UP_DOWN = 0x6
BUTTON_MY_UP_DOWN = 0x7
BUTTON_PROG = 0x8
BUTTON_SUN_FLAG = 0x9
BUTTON_FLAG = 0xA
BUTTON_STEP = 0xB
BUTTON_TOGGLE = 0xC
BUTTON_SENSOR = 0xE
BUTTON_RTW_PROTO = 0xF

# Command bytes reconstructed from 80-bit extensions
CMD_STEP_UP = 0x8B
CMD_FAVORITE = 0xC1
CMD_STOP = 0xF1

COMMAND_TO_BUTTON: dict[SomfyCommand, SomfyRTSButton] = {
    # The MY button stops the motor, so STOP maps to MY.
    SomfyCommand.MY: SomfyRTSButton.MY,
    SomfyCommand.STOP: SomfyRTSButton.MY,
    SomfyCommand.UP: SomfyRTSButton.UP,
    SomfyCommand.DOWN: SomfyRTSButton.DOWN,
    SomfyCommand.PROG: SomfyRTSButton.PROG,
}

BUTTON_TO_COMMAND: dict[int, SomfyCommand] = {
    BUTTON_MY: SomfyCommand.STOP,
    BUTTON_UP: SomfyCommand.UP,
    BUTTON_MY_UP: SomfyCommand.UP,
    BUTTON_DOWN: SomfyCommand.DOWN,
    BUTTON_MY_DOWN: SomfyCommand.DOWN,
    BUTTON_UP_DOWN: SomfyCommand.STOP,
    BUTTON_MY_UP_DOWN: SomfyCommand.STOP,
    BUTTON_PROG: SomfyCommand.PROG,
    BUTTON_STEP: SomfyCommand.STEP_DOWN,
    BUTTON_SENSOR: SomfyCommand.SENSOR,
    CMD_STEP_UP: SomfyCommand.STEP_UP,
    CMD_STOP: SomfyCommand.STOP,
    CMD_FAVORITE: SomfyCommand.MY,
}

# 80-bit control nibbles for step commands (direction lives in byte 8)
STEP_CTRL = BUTTON_STEP

# =============================================================================
# RF timings (microseconds), matching the native 56-bit encoder
# =============================================================================
WAKEUP_MARK_US = 9415
WAKEUP_SPACE_US = 89565
HW_SYNC_US = 2416
SW_SYNC_MARK_US = 4550
SW_SYNC_SPACE_US = 604
HALF_BIT_US = 604
INTER_FRAME_GAP_US = 30415
TOLERANCE_PCT = 25
RX_TOLERANCE_PCT = 30

# 80-bit variants: first frame carries 12 hardware sync pulses after
# the wake-up pulse, repeats 14; inter-frame silence reduced by 24
# symbols
HW_SYNC_FIRST_80BIT = 12
HW_SYNC_REPEAT_80BIT = 14
INTER_FRAME_GAP_80BIT_US = 15055

FRAME_BITS = 56
FRAME_BITS_80 = 80
EXT_BYTES = 3

# 80-bit extension byte 7 base value / increment per repeat
EXT_B7_BASE = 196
EXT_B7_STEP = 4
EXT_B7_STEP_CMDS = 132


def build_command(
    address: int,
    command: SomfyCommand,
    rolling_code: int,
    frame_repeats: int = 3,
) -> SomfyRTSCommand:
    """Build a native SomfyRTSCommand (56-bit RTS)."""
    if not 0 <= address <= 0xFFFFFF:
        raise ValueError(f"Address {address} out of 24-bit range")
    if not 0 <= rolling_code <= 0xFFFF:
        raise ValueError(f"Rolling code {rolling_code} out of 16-bit range")
    button = COMMAND_TO_BUTTON[command]
    return SomfyRTSCommand(
        address=address,
        rolling_code=rolling_code,
        button=button.value,
        frame_repeats=frame_repeats,
    )


def build_80bit_command(
    address: int,
    command: SomfyCommand,
    rolling_code: int,
    step_size: int = 0,
    frame_repeats: int = 3,
) -> OOKCommand:
    """Build an 80-bit RTS command (step-wheel capable devices)."""
    timings = build_80bit_timings(
        address, command, rolling_code, step_size, frame_repeats
    )
    return OOKCommand(frequency=RTS_FREQUENCY_HZ, timings=timings, repeat_count=0)


# =============================================================================
# 80-bit frame construction
# =============================================================================
def _checksum(frame: bytearray) -> int:
    """RTS checksum: XOR of all nibbles of the first 7 frame bytes."""
    cksum = 0
    for byte in frame:
        cksum ^= byte ^ (byte >> 4)
    return cksum & 0x0F


def _checksum80(b7: int, b8: int, b9: int) -> int:
    """80-bit extension checksum: XOR of the extension nibbles."""
    return (
        ((b7 & 0xF0) >> 4) ^ ((b8 & 0xF0) >> 4) ^ ((b9 & 0xF0) >> 4)
        ^ (b7 & 0x0F) ^ (b8 & 0x0F)
    ) & 0x0F


def _extension(command: SomfyCommand, repeat: int, step_size: int) -> tuple[int, int, int, int]:
    """Return (ctrl_nibble, b7, b8, b9) for an 80-bit frame.

    ``repeat`` is the frame index in the burst (0 = first frame). The b7
    repeat counter increases by 4 per repeated frame
    (``encode80Byte7``).
    """
    if command in (SomfyCommand.STEP_UP, SomfyCommand.STEP_DOWN):
        step = step_size if step_size > 0 else 1
        ctrl = STEP_CTRL
        b7 = EXT_B7_STEP_CMDS
        mask = 0x38 if command == SomfyCommand.STEP_UP else 0x30
        b8 = ((step & 0x70) >> 4) | mask
        b9 = (step & 0x0F) << 4
        return ctrl, b7, b8, b9

    if command == SomfyCommand.UP:
        ctrl, b8, b9 = BUTTON_UP, 0x20, 0x00
    elif command == SomfyCommand.DOWN:
        ctrl, b8, b9 = BUTTON_DOWN, 0x2C, 0x80
    elif command == SomfyCommand.PROG:
        ctrl, b8, b9 = BUTTON_PROG, 0x00, 0x10
    else:
        # MY / STOP
        ctrl, b8, b9 = BUTTON_MY, 0x00, 0x10

    b7 = EXT_B7_BASE
    r = repeat
    while (r * EXT_B7_STEP) + b7 > 255:
        r -= 15
    b7 += r * EXT_B7_STEP
    return ctrl, b7, b8, b9


def _build_80bit_frame(
    address: int,
    command: SomfyCommand,
    rolling_code: int,
    repeat: int,
    step_size: int,
) -> bytearray:
    """Build one 80-bit frame (obfuscated, checksums integrated)."""
    ctrl, b7, b8, b9 = _extension(command, repeat, step_size)

    frame = bytearray(10)
    frame[0] = 0xA7  # encryption key (fixed)
    frame[1] = ctrl << 4
    frame[2] = (rolling_code >> 8) & 0xFF
    frame[3] = rolling_code & 0xFF
    frame[4] = (address >> 16) & 0xFF
    frame[5] = (address >> 8) & 0xFF
    frame[6] = address & 0xFF

    # Standard checksum over the first 7 bytes (checksum nibble still zero)
    frame[1] |= _checksum(frame)

    # Extension bytes: NOT obfuscated, own checksum in the low nibble of b9
    frame[7] = b7
    frame[8] = b8
    frame[9] = (b9 & 0xF0) | _checksum80(b7, b8, b9)

    # Obfuscation only applies to bytes 1-6
    for i in range(1, 7):
        frame[i] ^= frame[i - 1]
    return frame


def build_80bit_timings(
    address: int,
    command: SomfyCommand,
    rolling_code: int,
    step_size: int = 0,
    frame_repeats: int = 3,
) -> list[int]:
    """Build the raw OOK timings of an 80-bit RTS transmission."""
    if not 0 <= address <= 0xFFFFFF:
        raise ValueError(f"Address {address} out of 24-bit range")
    if not 0 <= rolling_code <= 0xFFFF:
        raise ValueError(f"Rolling code {rolling_code} out of 16-bit range")
    if not 0 <= step_size <= 0x7F:
        raise ValueError(f"Step size {step_size} out of 7-bit range")

    timings: list[int] = []

    def add(us: int) -> None:
        """Append a run, merging consecutive same-polarity runs."""
        if timings and (us > 0) == (timings[-1] > 0):
            timings[-1] += us
        else:
            timings.append(us)

    def encode_frame(frame: bytearray, sync: int, first: bool) -> None:
        if first:
            add(WAKEUP_MARK_US)
            add(-WAKEUP_SPACE_US)
        for _ in range(sync):
            add(HW_SYNC_US)
            add(-HW_SYNC_US)
        add(SW_SYNC_MARK_US)
        add(-SW_SYNC_SPACE_US)
        for b in range(FRAME_BITS_80):
            if (frame[b // 8] >> (7 - b % 8)) & 1:
                add(-HALF_BIT_US)
                add(HALF_BIT_US)
            else:
                add(HALF_BIT_US)
                add(-HALF_BIT_US)
        add(-INTER_FRAME_GAP_80BIT_US)

    encode_frame(_build_80bit_frame(address, command, rolling_code, 0, step_size), HW_SYNC_FIRST_80BIT, True)
    for repeat in range(1, frame_repeats + 1):
        encode_frame(
            _build_80bit_frame(address, command, rolling_code, repeat, step_size),
            HW_SYNC_REPEAT_80BIT,
            False,
        )
    return timings


# =============================================================================
# Decoder (raw timings -> frame), 56 and 80 bit
# =============================================================================
@dataclass(frozen=True)
class RTSFrame:
    """A decoded Somfy RTS frame."""

    address: int
    control: int
    rolling_code: int
    command: SomfyCommand | None
    bits: int = FRAME_BITS
    step: int = 0


def _within(duration: int, expected: int, tolerance_pct: int) -> bool:
    lower = expected * (100 - tolerance_pct) / 100
    upper = expected * (100 + tolerance_pct) / 100
    return lower <= duration <= upper


def _build_frame_from_bytes(plain: bytearray) -> bytearray:
    """Apply RTS checksum and obfuscation to a plain 7-byte frame."""
    frame = bytearray(plain[:7])
    frame[1] |= _checksum(frame)
    for i in range(1, 7):
        frame[i] ^= frame[i - 1]
    return frame


def _frame_checksum(frame: bytearray) -> int:
    """Checksum over the first 7 bytes with the checksum nibble masked out."""
    cksum = 0
    for byte in frame[:7]:
        cksum ^= byte ^ (byte >> 4)
    return cksum & 0x0F


def _build_frame_bytes(levels: list[bool], bits: int) -> bytearray | None:
    """Decode Manchester bits from a half-bit level stream.

    Bit 0 = HIGH then LOW, bit 1 = LOW then HIGH (rising edge mid-bit).
    """
    frame = bytearray((bits + 7) // 8)
    for b in range(bits):
        first = levels[2 * b]
        second = levels[2 * b + 1]
        if first == second:
            return None
        if second:  # LOW then HIGH
            frame[b // 8] |= 0x80 >> (b % 8)
    return frame


def _hw_run_count(timings: list[int], sw_sync_index: int) -> int:
    """Count hardware sync runs immediately before the software sync."""
    runs = 0
    i = sw_sync_index - 1
    while i >= 0 and _within(abs(timings[i]), HW_SYNC_US, TOLERANCE_PCT):
        runs += 1
        i -= 1
    return runs


def _bit_length_for_hw_runs(runs: int) -> int:
    """Map the hardware sync run count to the frame bit length.

    56-bit frames: 4 runs (first frame, 2 pulses) or 14 runs (repeats,
    7 pulses). 80-bit frames: 12/13 runs (step-wheel remotes, 6 pulses)
    or more than 17 runs (gateway TX: 24/28 runs).
    """
    if runs <= 7:
        return FRAME_BITS
    if 12 <= runs <= 13:
        return FRAME_BITS_80
    if runs > 17:
        return FRAME_BITS_80
    return FRAME_BITS


def _translate_control(control: int, frame: bytearray, bits: int) -> tuple[SomfyCommand | None, int]:
    """Translate the control nibble into a command, applying 80-bit extensions."""
    if bits != FRAME_BITS_80:
        return BUTTON_TO_COMMAND.get(control), 0
    if control == BUTTON_MY:
        # MY extension: Stop (0xF1) / Favorite (0xC1) live in byte 8
        return BUTTON_TO_COMMAND.get(control | ((frame[8] & 0x0F) << 4)), 0
    if control == STEP_CTRL:
        # Step direction lives in bit 3 of byte 8, size in bytes 8-9
        command = BUTTON_TO_COMMAND.get(control | ((frame[8] & 0x08) << 4))
        step = ((frame[8] & 0x07) << 4) | ((frame[9] & 0xF0) >> 4)
        return command, step
    # 0xF with key 0xA4 is Toggle; RTW/RTV frames also use 0xF. Position
    # semantics are unknown: leave the command unmapped.
    return BUTTON_TO_COMMAND.get(control), 0


def decode_timings(timings: list[int]) -> RTSFrame | None:
    """Decode a Somfy RTS frame (56 or 80 bit) from raw OOK timings.

    Timings alternate mark (positive) and space (negative) microseconds.
    Manchester-coded data merges consecutive same-level half-bits (a 0
    followed by a 1 produces a double-width low), so the decoder walks
    runs of equal level instead of fixed-size slots.
    """
    n = len(timings)
    if n < 8:
        return None

    # Locate the software sync: a ~4550 us mark preceded by hardware sync.
    for i in range(1, n - 1):
        if timings[i] <= 0 or timings[i - 1] >= 0:
            continue
        if not _within(timings[i], SW_SYNC_MARK_US, TOLERANCE_PCT):
            continue
        hw_runs = _hw_run_count(timings, i)
        if hw_runs < 4:
            continue

        bits = _bit_length_for_hw_runs(hw_runs)
        units_needed = bits * 2 + 1

        # Expand the runs after the sync into a half-bit level stream.
        # The first low run holds the sync space (604 us) and possibly a
        # merged first data half-bit, so one low unit is always skipped.
        levels: list[bool] = []
        for t in timings[i + 1 :]:
            duration = abs(t)
            count = round(duration / HALF_BIT_US)
            if count > 2:
                # Inter-frame gap: when the frame ends with a LOW half-bit
                # it merges with the gap, leaving us one unit short.
                if t < 0 and len(levels) == units_needed - 1:
                    levels.append(False)
                break
            if count < 1:
                break
            if not _within(duration, count * HALF_BIT_US, RX_TOLERANCE_PCT):
                levels = []
                break
            levels.extend([t > 0] * count)
            if len(levels) >= units_needed:
                break

        if len(levels) < units_needed:
            if len(levels) == units_needed - 1:
                # A capture from remote_receiver carries no inter-frame
                # gap run: a trailing LOW half-bit merges with the
                # silence and never appears in the capture. Complete the
                # stream (the Manchester and checksum checks reject
                # anything else).
                levels.append(False)
            else:
                continue
        data_levels = levels[1:units_needed]
        frame = _build_frame_bytes(data_levels, bits)
        if frame is None:
            continue

        # De-obfuscate the rolling XOR applied after the key byte. The
        # obfuscation runs forward, so undoing it must run backward.
        # Extension bytes (7-9) are never obfuscated.
        for b in range(6, 0, -1):
            frame[b] ^= frame[b - 1]

        # Verify the standard checksum (checksum nibble masked out).
        stored = frame[1] & 0x0F
        frame[1] &= 0xF0
        if _frame_checksum(frame) != stored:
            continue
        frame[1] |= stored

        # Verify the 80-bit extension checksum.
        if bits == FRAME_BITS_80:
            if (frame[9] & 0x0F) != _checksum80(frame[7], frame[8], frame[9]):
                continue

        control = frame[1] >> 4
        command, step = _translate_control(control, frame, bits)
        return RTSFrame(
            address=(frame[4] << 16) | (frame[5] << 8) | frame[6],
            control=control,
            rolling_code=(frame[2] << 8) | frame[3],
            command=command,
            bits=bits,
            step=step,
        )
    return None
