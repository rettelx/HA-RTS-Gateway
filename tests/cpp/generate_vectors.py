"""Regenerate tests/cpp/vectors.hpp for the C++ decoder harness.

The expected fields of every vector are computed by the Python decoder
(single source of truth), so the C++ twin is validated against it. Run:

    python tests/cpp/generate_vectors.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from custom_components.rts_gateway.const import SomfyCommand
from custom_components.rts_gateway.protocols import (
    build_80bit_timings,
    build_command,
    decode_timings,
)

# Real captures from an ESPHome remote_receiver (physical Somfy remotes,
# ~5% clock drift, trailing LOW half-bit merged with the silence).
REAL_CAPTURES = {
    "remote1-up-1301": [
        2486, -2533, 2480, -2506, 4814, -1273, 1267, -1292, 1276, -635,
        649, -1295, 1275, -1271, 647, -649, 1278, -644, 639, -622,
        665, -1274, 1292, -1270, 635, -639, 657, -615, 1302, -641,
        644, -639, 645, -1275, 644, -619, 662, -643, 1265, -1292,
        1268, -649, 649, -1268, 658, -619, 1296, -1263, 660, -639,
        633, -639, 647, -642, 640, -649, 1277, -1273, 664, -620,
        646, -642, 646, -639, 646, -623, 661, -619, 1300, -621,
        648, -1273, 1295, -1266, 1307, -1278, 1289, -1268, 1269, -1280,
        1292, -1274, 1296,
    ],
    "remote1-up-1303": [
        2494, -2531, 2479, -2499, 4804, -1293, 1267, -1296, 1267, -644,
        640, -1276, 668, -629, 638, -646, 639, -622, 1294, -639,
        640, -649, 648, -1268, 1283, -629, 660, -1273, 640, -654,
        1287, -613, 663, -637, 650, -1268, 639, -642, 1287, -612,
        676, -1266, 1285, -635, 640, -1294, 644, -619, 1289, -1275,
        667, -629, 638, -641, 634, -636, 651, -643, 1294, -1268,
        658, -619, 647, -630, 661, -621, 660, -615, 666, -625,
        1297, -617, 667, -1264, 1295, -1267, 1286, -1293, 1275, -1272,
        1299, -1276, 1273, -1288, 1277,
    ],
    "remote1-up-1305": [
        2489, -2503, 2499, -2499, 4805, -1268, 1286, -1275, 1292, -1265,
        1308, -629, 640, -1289, 636, -621, 1314, -624, 635, -653,
        638, -642, 648, -1273, 646, -640, 640, -634, 635, -651,
        1269, -653, 636, -649, 637, -640, 636, -640, 648, -1269,
        1287, -1275, 1288, -636, 650, -1271, 656, -640, 1295, -1249,
        648, -639, 652, -643, 641, -636, 648, -640, 1270, -1275,
        669, -618, 657, -621, 665, -624, 641, -650, 641, -644,
        1274, -642, 647, -1264, 1281, -1292, 1274, -1295, 1278, -1285,
        1270, -1284, 1291, -1273, 1296,
    ],
    "remote2-noisy-527": [
        2523, -2632, 2490, -2611, 4832, -1310, 1279, -1286, 1299, -1292,
        1268, -1323, 1260, -1300, 1299, -638, 629, -676, 630, -1308,
        1246, -689, 613, -688, 614, -1316, 1270, -644, 643, -650,
        637, -1301, 1275, -1307, 1287, -1293, 1261, -684, 632, -660,
        627, -640, 638, -1319, 1273, -1314, 598, -679, 1280, -1291,
        615, -667, 1276, -672, 624, -669, 640, -640, 622, -1306,
        647, -664, 1278, -640, 649, -641, 641, -667, 622, -1321,
        614, -659, 616, -665, 1270, -662, 647, -645, 620, -1312,
        1300, -645, 641, -664, 638,
    ],
    "remote2-up-529": [
        2529, -2628, 2503, -2597, 4847, -1288, 1290, -1311, 1277, -1275,
        646, -646, 1275, -677, 640, -1290, 1270, -668, 648, -625,
        639, -678, 618, -1301, 644, -647, 639, -666, 630, -641,
        1270, -672, 626, -664, 631, -668, 623, -1298, 1296, -1271,
        647, -664, 1283, -647, 627, -1291, 1290, -1310, 1261, -672,
        646, -1274, 1294, -1292, 1294, -645, 643, -649, 639, -646,
        633, -1296, 634, -666, 647, -621, 1300, -1305, 1277, -643,
        650, -1296, 1279, -1300, 1287, -645, 639, -1301, 616, -668,
        1291, -636, 641, -1292, 632,
    ],
    "remote2-repeat-530": [
        2527, -2618, 2518, -2576, 4863, -1274, 1296, -1298, 1278, -1281,
        666, -647, 1270, -1289, 650, -646, 1296, -646, 649, -647,
        642, -632, 669, -1275, 1296, -639, 639, -1280, 1319, -632,
        635, -650, 641, -647, 648, -1297, 652, -641, 1285, -1294,
        1296, -640, 644, -1294, 1279, -1301, 1274, -638, 652, -1290,
        1308, -1261, 1314, -631, 642, -665, 651, -639, 629, -1292,
        648, -639, 655, -645, 1287, -1290, 1296, -636, 650, -1296,
        1274, -1294, 1296, -641, 645, -1293, 652, -614, 1301, -645,
        660, -1271, 622,
    ],
}

# Generated cases: (command, address, rolling_code, cpp_command_byte)
GENERATED_56BIT = [
    (SomfyCommand.UP, 0x101010, 1, 0x2),
    (SomfyCommand.DOWN, 12345678, 300, 0x4),
    (SomfyCommand.STOP, 0xFFFFFF, 65535, 0x1),
    (SomfyCommand.MY, 0, 2, 0x1),
    (SomfyCommand.PROG, 0xABCDEF, 42, 0x8),
]

GENERATED_80BIT = [
    (SomfyCommand.UP, 0x101010, 1, 0, 0x2),
    (SomfyCommand.DOWN, 12345678, 300, 0, 0x4),
    (SomfyCommand.MY, 0xFFFFFF, 65535, 0, 0x1),
    (SomfyCommand.PROG, 0xABCDEF, 42, 0, 0x8),
    (SomfyCommand.STEP_UP, 0x222222, 55, 123, 0x8B),
    (SomfyCommand.STEP_DOWN, 0x222222, 56, 1, 0x0B),
]


def to_esphome_captures(timings):
    """Split a burst like remote_receiver: silence (> 10 ms) ends a
    capture and is not recorded; a trailing LOW half-bit disappears."""
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
    return [c for c in captures if len(c) >= 20]


def build_vectors():
    vectors = []

    for name, timings in REAL_CAPTURES.items():
        frame = decode_timings(timings)
        assert frame is not None, name
        vectors.append((name, timings, frame.address, frame.control,
                        frame.rolling_code, frame.bits, 0))

    for command, address, rc, cpp_cmd in GENERATED_56BIT:
        timings = build_command(address, command, rc).get_raw_timings()
        for i, capture in enumerate(to_esphome_captures(timings)):
            frame = decode_timings(capture)
            assert frame is not None and frame.address == address, command
            vectors.append((f"56bit-{command.value.lower()}-{address}-{i}",
                            capture, frame.address, cpp_cmd,
                            frame.rolling_code, frame.bits, 0))

    for command, address, rc, step_size, cpp_cmd in GENERATED_80BIT:
        timings = build_80bit_timings(address, command, rc, step_size=step_size)
        for i, capture in enumerate(to_esphome_captures(timings)):
            frame = decode_timings(capture)
            assert frame is not None and frame.address == address, command
            expected_step = step_size if command.value.startswith("STEP") else 0
            vectors.append((f"80bit-{command.value.lower()}-{address}-{i}",
                            capture, frame.address, cpp_cmd,
                            frame.rolling_code, frame.bits, expected_step))

    # A corrupted frame must not decode (verified against the Python twin).
    good = vectors[0][1]
    bad = list(good)
    bad[len(bad) // 2] = -bad[len(bad) // 2]
    assert decode_timings(bad) is None, "corruption vector unexpectedly decodes"
    return vectors


def main() -> None:
    vectors = build_vectors()
    lines = [
        "// Generated by tests/cpp/generate_vectors.py - do not edit.",
        "#pragma once",
        "#include <cstdint>",
        "#include <vector>",
        "",
        "struct TestVector {",
        "  const char *name;",
        "  std::vector<int32_t> timings;",
        "  uint32_t address;",
        "  uint8_t command;",
        "  uint16_t rolling_code;",
        "  uint32_t bits;",
        "  uint16_t step;",
        "};",
        "",
        "static const TestVector TEST_VECTORS[] = {",
    ]
    for name, timings, address, command, rc, bits, step in vectors:
        values = ", ".join(str(t) for t in timings)
        lines.append(
            f'  {{"{name}", {{{values}}}, {address}, 0x{command:02X}, '
            f"{rc}, {bits}, {step}}},"
        )
    lines.append("};")
    lines.append(f"static const size_t TEST_VECTOR_COUNT = {len(vectors)};")
    out = Path(__file__).parent / "vectors.hpp"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(vectors)} vectors)")


if __name__ == "__main__":
    main()
