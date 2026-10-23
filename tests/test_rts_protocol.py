"""Tests for the Somfy RTS protocol module.

Validates the timing decoder against the native rf-protocols encoder
(the library shipped with the radio_frequency integration), which is
the authoritative RTS implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.somfy_rts.const import SomfyCommand
from custom_components.somfy_rts.protocols import (
    BUTTON_DOWN,
    BUTTON_MY,
    BUTTON_PROG,
    BUTTON_UP,
    build_80bit_command,
    build_80bit_timings,
    build_command,
    decode_timings,
)

WAKEUP_MARK_US = 9415
HW_SYNC_US = 2416
SW_SYNC_MARK_US = 4550
HALF_BIT_US = 604


@pytest.mark.parametrize(
    ("command", "button", "address", "rolling_code"),
    [
        (SomfyCommand.UP, BUTTON_UP, 0x101010, 1),
        (SomfyCommand.DOWN, BUTTON_DOWN, 12345678, 300),
        (SomfyCommand.STOP, BUTTON_MY, 0xFFFFFF, 65535),
        (SomfyCommand.MY, BUTTON_MY, 0, 2),
        (SomfyCommand.PROG, BUTTON_PROG, 0xABCDEF, 42),
    ],
)
def test_round_trip(command, button, address, rolling_code):
    """Decoding the encoder output must restore the frame fields."""
    timings = build_command(address, command, rolling_code).get_raw_timings()
    frame = decode_timings(timings)
    assert frame is not None, "decoder failed to find a valid frame"
    assert frame.address == address
    assert frame.control == button
    assert frame.rolling_code == rolling_code


def test_stop_and_my_share_button():
    """STOP and MY are the same RTS button."""
    timings = build_command(0x1234, SomfyCommand.STOP, 7).get_raw_timings()
    frame = decode_timings(timings)
    assert frame.control == BUTTON_MY
    assert frame.command == SomfyCommand.STOP


def test_encoder_shape():
    """The encoder output must start with the wake-up pulse."""
    timings = build_command(0x42, SomfyCommand.UP, 3).get_raw_timings()
    assert timings[0] == WAKEUP_MARK_US
    # alternating mark/space
    assert all(
        (t > 0) != (timings[i - 1] > 0) for i, t in enumerate(timings[1:], 1)
    ), "timings must alternate"


def test_decoder_tolerates_noise_prefix():
    """A noisy leading run must not prevent decoding."""
    timings = build_command(0x555555, SomfyCommand.DOWN, 10).get_raw_timings()
    noisy = [200, -150, 3000, -800] + timings
    frame = decode_timings(noisy)
    assert frame is not None
    assert frame.address == 0x555555
    assert frame.rolling_code == 10


def test_decoder_rejects_garbage():
    """Random timings must not decode."""
    assert decode_timings([]) is None
    assert decode_timings([100, -100] * 60) is None
    assert decode_timings([9415, -885] + [604, -604] * 60) is None


def test_decoder_survives_jitter():
    """±20% jitter on every timing must still decode (RX tolerance)."""
    timings = build_command(0x123412, SomfyCommand.UP, 5).get_raw_timings()
    jittered = [
        int(t * (1.2 if (i % 3 == 0) else 0.85)) for i, t in enumerate(timings)
    ]
    frame = decode_timings(jittered)
    assert frame is not None
    assert frame.address == 0x123412
    assert frame.rolling_code == 5


def test_command_raises_on_bad_values():
    """Out of range values must be rejected."""
    from rf_protocols.commands.somfy_rts import SomfyRTSCommand  # noqa: F401

    with pytest.raises(ValueError):
        build_command(1 << 24, SomfyCommand.UP, 1)
    with pytest.raises(ValueError):
        build_command(0x123, SomfyCommand.UP, 1 << 16)


def test_second_frame_of_burst_decodes():
    """Repeater frames (7 hardware syncs) must decode too."""
    timings = build_command(0x999999, SomfyCommand.DOWN, 123).get_raw_timings()
    # The burst is: wake-up frame, then repeated frames separated by
    # inter-frame gaps. Skip past the first frame: locate the second
    # software sync occurrence.
    decoded = decode_timings(timings)
    assert decoded is not None
    # Feeding only the tail of the burst (a repeat frame) must also work:
    # find the inter-frame gap after the first frame.
    gap_index = next(
        i
        for i, t in enumerate(timings)
        if t < 0 and abs(t) > 20000
    )
    tail = timings[gap_index + 1 :]
    frame = decode_timings(tail)
    assert frame is not None
    assert frame.address == 0x999999
    assert frame.rolling_code == 123


# =============================================================================
# 80-bit protocol (step-wheel remotes, e.g. Somfy Modulis/Situo)
# =============================================================================


@pytest.mark.parametrize(
    ("command", "ctrl", "address", "rolling_code"),
    [
        (SomfyCommand.UP, BUTTON_UP, 0x101010, 1),
        (SomfyCommand.DOWN, BUTTON_DOWN, 12345678, 300),
        (SomfyCommand.STOP, BUTTON_MY, 0xFFFFFF, 65535),
        (SomfyCommand.PROG, BUTTON_PROG, 0xABCDEF, 42),
    ],
)
def test_round_trip_80bit(command, ctrl, address, rolling_code):
    """Decoding our 80-bit encoder output must restore the frame fields."""
    timings = build_80bit_timings(address, command, rolling_code)
    frame = decode_timings(timings)
    assert frame is not None, "decoder failed to find a valid 80-bit frame"
    assert frame.bits == 80
    assert frame.address == address
    assert frame.control == ctrl
    assert frame.rolling_code == rolling_code


def test_80bit_step_commands_round_trip():
    """Step commands carry the direction in byte 8 and the size in 8-9."""
    timings = build_80bit_timings(
        0x222222, SomfyCommand.STEP_UP, 55, step_size=123
    )
    frame = decode_timings(timings)
    assert frame is not None
    assert frame.bits == 80
    assert frame.command == SomfyCommand.STEP_UP
    assert frame.step == 123

    timings = build_80bit_timings(
        0x222222, SomfyCommand.STEP_DOWN, 56, step_size=1
    )
    frame = decode_timings(timings)
    assert frame is not None
    assert frame.command == SomfyCommand.STEP_DOWN
    assert frame.step == 1


def test_80bit_stop_extension_decodes_as_stop():
    """80-bit STOP frames (My + byte 8 low nibble 0xF) must map to STOP."""
    # Encode a MY command with a manually forged Stop extension
    from custom_components.somfy_rts.protocols.somfy_rts import (
        HW_SYNC_FIRST_80BIT,
        _build_80bit_frame,
    )
    frame = _build_80bit_frame(0x333333, SomfyCommand.MY, 9, 0, 0)
    # Forge the Stop extension: b8=47 (0x2F), recompute cs80
    frame[8] = 47
    frame[9] = 0xF0 | 0x00
    from custom_components.somfy_rts.protocols.somfy_rts import _checksum80
    frame[9] = 0xF0 | _checksum80(frame[7], frame[8], frame[9])
    # Rebuild timings manually around this frame
    timings = [9415, -89565]
    timings += [2416, -2416] * HW_SYNC_FIRST_80BIT
    timings += [4550, -604]
    for b in range(80):
        if (frame[b // 8] >> (7 - b % 8)) & 1:
            timings += [-604, 604]
        else:
            timings += [604, -604]
    timings += [-15055]

    decoded = decode_timings(timings)
    assert decoded is not None
    assert decoded.bits == 80
    assert decoded.command == SomfyCommand.STOP


def test_80bit_repeat_frames_have_incrementing_b7():
    """Each repeated frame must bump the extension counter (byte 7)."""
    from custom_components.somfy_rts.protocols.somfy_rts import (
        _build_80bit_frame,
    )
    frames = [
        _build_80bit_frame(0x42, SomfyCommand.UP, 3, repeat, 0)
        for repeat in range(3)
    ]
    assert frames[0][7] == 196
    assert frames[1][7] == 200
    assert frames[2][7] == 204


def test_80bit_frame_checks_both_checksums():
    """A corrupted 80-bit frame must be rejected by the checksums."""
    from custom_components.somfy_rts.protocols.somfy_rts import (
        _build_80bit_frame,
    )

    frame = _build_80bit_frame(0x777777, SomfyCommand.UP, 10, 0, 0)
    timings = [2416, -2416] * 6 + [4550, -604]
    for b in range(80):
        if (frame[b // 8] >> (7 - b % 8)) & 1:
            timings += [-604, 604]
        else:
            timings += [604, -604]
    timings += [-15055]
    assert decode_timings(timings) is not None

    # Corrupt the extension: flip one data half-bit inside byte 9
    # (bits 72..79): the 80-bit checksum (or the standard one) must fail.
    bit_index = 76  # inside byte 9
    pos = 8 + 2 * 6 + 2 * bit_index
    bad = list(timings)
    bad[pos], bad[pos + 1] = bad[pos + 1], bad[pos]
    assert decode_timings(bad) is None


def test_real_molette_style_frame_decodes():
    """Real step-wheel remotes send 6 hw pulses (12 runs): must decode."""
    from custom_components.somfy_rts.protocols.somfy_rts import (
        _build_80bit_frame,
    )
    frame = _build_80bit_frame(0x999999, SomfyCommand.DOWN, 123, 0, 0)
    # 6 pulses = 12 runs, no wake-up (repeat frame style)
    timings = [2416, -2416] * 6
    timings += [4550, -604]
    for b in range(80):
        if (frame[b // 8] >> (7 - b % 8)) & 1:
            timings += [-604, 604]
        else:
            timings += [604, -604]
    timings += [-15055]
    decoded = decode_timings(timings)
    assert decoded is not None
    assert decoded.bits == 80
    assert decoded.address == 0x999999
    assert decoded.command == SomfyCommand.DOWN


def test_80bit_command_wraps_ook():
    """build_80bit_command returns a usable RadioFrequencyCommand."""
    cmd = build_80bit_command(0x555555, SomfyCommand.UP, 7)
    timings = cmd.get_raw_timings()
    assert timings[0] == WAKEUP_MARK_US
    frame = decode_timings(timings)
    assert frame is not None
    assert frame.address == 0x555555


def test_56bit_still_decodes_after_80bit_support():
    """Sanity: the 56-bit native encoder output must keep decoding."""
    timings = build_command(0x123, SomfyCommand.UP, 5).get_raw_timings()
    frame = decode_timings(timings)
    assert frame is not None
    assert frame.bits == 56
    assert frame.address == 0x123


def test_sensor_frame_decodes():
    """Sensor frames (ctrl 0xE) must map to the SENSOR command."""
    from custom_components.somfy_rts.protocols.somfy_rts import (
        BUTTON_SENSOR,
        _build_frame_from_bytes,
    )
    # A sensor frame: ctrl 0xE, "rolling code" field = wind|sun payload
    plain = bytearray(7)
    plain[0] = 0xA7
    plain[1] = BUTTON_SENSOR << 4
    payload = 0x03  # wind + sun bits
    plain[2] = (payload >> 8) & 0xFF
    plain[3] = payload & 0xFF
    plain[4] = 0x11
    plain[5] = 0x22
    plain[6] = 0x33
    obf = _build_frame_from_bytes(plain)

    timings = [2416, -2416] * 2 + [4550, -604]
    for b in range(56):
        if (obf[b // 8] >> (7 - b % 8)) & 1:
            timings += [-604, 604]
        else:
            timings += [604, -604]
    timings += [-30415]

    frame = decode_timings(timings)
    assert frame is not None
    assert frame.command == SomfyCommand.SENSOR
    assert frame.rolling_code == payload
    assert frame.address == 0x112233


# Real-world capture from an ESPHome remote_receiver (Somfy physical
# remote, ~5% clock drift). The trailing LOW half-bit is absent because
# it merges with the inter-frame silence.
REAL_REMOTE_CAPTURE = [
    2486, -2533, 2480, -2506, 4814, -1273, 1267, -1292, 1276, -635,
    649, -1295, 1275, -1271, 647, -649, 1278, -644, 639, -622,
    665, -1274, 1292, -1270, 635, -639, 657, -615, 1302, -641,
    644, -639, 645, -1275, 644, -619, 662, -643, 1265, -1292,
    1268, -649, 649, -1268, 658, -619, 1296, -1263, 660, -639,
    633, -639, 647, -642, 640, -649, 1277, -1273, 664, -620,
    646, -642, 646, -639, 646, -623, 661, -619, 1300, -621,
    648, -1273, 1295, -1266, 1307, -1278, 1289, -1268, 1269, -1280,
    1292, -1274, 1296,
]


def _to_esphome_captures(timings):
    """Split a burst like remote_receiver does: silence (> 10 ms) ends
    a capture and is not recorded, so a trailing LOW half-bit that
    merges with the silence disappears from the capture."""
    captures, current = [], []
    for t in timings:
        if abs(t) > 10000:
            if current:
                if current[-1] < 0:
                    current.pop()
                captures.append(current)
            current = []
        else:
            current.append(t)
    if current:
        if current[-1] < 0:
            current.pop()
        captures.append(current)
    return captures


def test_decode_real_remote_capture():
    """Regression: real captures lack the trailing LOW half-bit."""
    frame = decode_timings(REAL_REMOTE_CAPTURE)
    assert frame is not None
    assert frame.address == 7611744
    assert frame.command == SomfyCommand.UP
    assert frame.rolling_code == 1301
    assert frame.bits == 56


@pytest.mark.parametrize(
    ("command", "button", "address", "rolling_code"),
    [
        (SomfyCommand.UP, BUTTON_UP, 0x101010, 1),
        (SomfyCommand.DOWN, BUTTON_DOWN, 12345678, 300),
        (SomfyCommand.STOP, BUTTON_MY, 0xFFFFFF, 65535),
        (SomfyCommand.MY, BUTTON_MY, 0, 2),
        (SomfyCommand.PROG, BUTTON_PROG, 0xABCDEF, 42),
    ],
)
def test_every_frame_of_burst_decodes_from_capture(command, button, address, rolling_code):
    """Each frame of a burst, as captured by remote_receiver (no gap
    runs, trailing LOW half-bit swallowed), must decode."""
    timings = build_command(address, command, rolling_code).get_raw_timings()
    decoded = 0
    for capture in _to_esphome_captures(timings):
        if len(capture) < 20:
            continue  # wake-up pulse fragment
        frame = decode_timings(capture)
        assert frame is not None
        assert frame.address == address
        # MY and STOP share the same RTS button.
        assert frame.command in (command, SomfyCommand.STOP)
        assert frame.rolling_code == rolling_code
        decoded += 1
    assert decoded >= 3  # first frame + repeats


def test_capture_short_by_more_than_one_unit_rejected():
    """Only the exact single trailing half-bit may be missing."""
    timings = build_command(0x101010, SomfyCommand.UP, 5).get_raw_timings()
    capture = next(c for c in _to_esphome_captures(timings) if len(c) >= 20)
    if capture[-1] > 0:
        capture = capture[:-1]
    capture = capture[:-1]  # one extra unit missing
    assert decode_timings(capture) is None


# =============================================================================
# Validation d'entree et bordures du protocole
# =============================================================================
def test_build_80bit_timings_validates_inputs():
    """Adresses, codes et pas hors bornes : ValueError."""
    import pytest

    with pytest.raises(ValueError):
        build_80bit_timings(
            0x1000000, SomfyCommand.UP, 1
        )
    with pytest.raises(ValueError):
        build_80bit_timings(
            0x1234, SomfyCommand.UP, 0x10000
        )
    with pytest.raises(ValueError):
        build_80bit_timings(
            0x1234, SomfyCommand.UP, 1, step_size=0x80
        )


def test_extension_byte7_wraps_on_high_repeats():
    """Le compteur de repetition boucle sur 15 (b7 <= 255)."""
    # 20 repetitions depassent la plage de l'octet 7 : le compteur redemarre.
    timings = build_80bit_timings(
        0x1234, SomfyCommand.UP, 42, frame_repeats=20
    )
    assert timings  # la trame se construit malgre le debordement


def test_decode_rejects_short_hw_sync():
    """Synchro materielle trop courte : trame ignoree."""
    from custom_components.somfy_rts.protocols.somfy_rts import (
        HALF_BIT_US,
        SW_SYNC_MARK_US,
        SW_SYNC_SPACE_US,
    )

    # Une seule impulsion de synchro materielle avant la synchro logicielle.
    timings = [
        2412, -2412,  # wake-up
        SW_SYNC_MARK_US, -SW_SYNC_SPACE_US,  # synchro logicielle
    ] + [HALF_BIT_US, -HALF_BIT_US] * 8
    assert decode_timings(timings) is None
