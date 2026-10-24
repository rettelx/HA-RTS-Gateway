"""Compile and run the C++ decoder test harness.

The harness (tests/cpp/test_decoder.cpp) runs the REAL decoder from
esphome_components/rts_gateway against vectors generated from the Python
twin (tests/cpp/generate_vectors.py), including captures of real Somfy
remotes. Requires the ``ziglang`` package as a host C++ compiler.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_CPP = Path(__file__).parent / "cpp"
REPO = Path(__file__).parent.parent


def _zig_available() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "ziglang", "version"],
            capture_output=True,
            timeout=60,
            check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _zig_available(), reason="ziglang not installed")
def test_cpp_decoder(tmp_path):
    # 1. Regenerate the vectors from the Python twin.
    subprocess.run(
        [sys.executable, str(TESTS_CPP / "generate_vectors.py")],
        check=True,
        capture_output=True,
        timeout=120,
    )

    # 2. Compile the harness against the real decoder.
    exe = tmp_path / "test_decoder.exe"
    compile_cmd = [
        sys.executable, "-m", "ziglang", "c++",
        "-std=c++17", "-w",
        "-I", str(TESTS_CPP / "stubs"),
        "-I", str(REPO / "esphome_components" / "rts_gateway"),
        str(TESTS_CPP / "test_decoder.cpp"),
        str(REPO / "esphome_components" / "rts_gateway" / "rts_gateway.cpp"),
        "-o", str(exe),
    ]
    result = subprocess.run(compile_cmd, check=False, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, f"compilation failed:\n{result.stderr}"

    # 3. Run it.
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120, check=False)
    assert run.returncode == 0, (
        f"C++ decoder test failed:\n{run.stdout}\n{run.stderr}"
    )
    assert "ALL PASS" in run.stdout
