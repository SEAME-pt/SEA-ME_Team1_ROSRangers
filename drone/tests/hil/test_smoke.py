"""HIL smoke test: the spare ESP boots the flashed firmware and stays up.

The spare ESP sits on the Pi's USB (/dev/hil-esp, FTDI auto-reset on
DTR/RTS). The firmware prints nothing on UART0, so after the boot ROM banner
(74880 baud) the line must stay silent at 115200: any bytes there are a crash
dump or a reboot.

Without the board the hardware test is skipped, unless HIL_REQUIRED=1 (set in
hil.yaml) turns the skip into a failure.
"""

import os
import re
import sys
import time
from pathlib import Path

import pytest

PORT = os.environ.get("HIL_ESP_PORT", "/dev/hil-esp")
ROM_BAUD = 74880
APP_BAUD = 115200
ROM_WINDOW_S = 2.0
SILENT_WINDOW_S = 10.0
BOOT_RESETS = 3
BOOT_WINDOW_S = 3.0
# MSP v1 and v2 frame starts, in every direction
MSP_HEADERS = (b"$M<", b"$M>", b"$M!", b"$X<", b"$X>", b"$X!")

BOOT_MODE = re.compile(rb"boot mode:\((\d+),")
CRASH_MARKERS = (
    b"Exception (",
    b"CUT HERE FOR EXCEPTION DECODER",
    b"Soft WDT reset",
    b"rst cause:4",
)


def boot_mode(rom_output: bytes) -> int | None:
    """First `boot mode:(N,...)` in the ROM banner. 3 = normal boot from flash."""
    match = BOOT_MODE.search(rom_output)
    return int(match.group(1)) if match else None


def crash_markers(output: bytes) -> list[bytes]:
    return [marker for marker in CRASH_MARKERS if marker in output]


def msp_like(output: bytes) -> list:
    """MSP headers in `output`, plus any valid MSP v1 frame the FC would take."""
    # The bench tools' parser, the one tests/scripts/test_msp.py covers
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from msp import Parser

    return [h for h in MSP_HEADERS if h in output] + Parser().feed(output)


def read_for(port, seconds: float) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        data += port.read(port.in_waiting or 1)
    return bytes(data)


def test_boot_mode_parses_rom_banner():
    banner = b"\r\n ets Jan  8 2013,rst cause:2, boot mode:(3,6)\r\n\r\nload 0x4010f000"
    assert boot_mode(banner) == 3
    assert boot_mode(b"garbage") is None


def test_crash_markers_found():
    dump = b"\r\nException (28):\r\nepc1=0x40201234\r\n"
    assert crash_markers(dump) == [b"Exception ("]
    assert crash_markers(b"") == []


def test_msp_like_finds_headers_and_frames():
    assert msp_like(b"\r\n ets Jan  8 2013,rst cause:2, boot mode:(3,6)\r\n") == []
    found = msp_like(b"noise$M<\x00\x01\x01noise")  # MSP_API_VERSION request
    assert found[0] == b"$M<" and found[1].cmd == 1
    assert msp_like(b"$X>") == [b"$X>"]


def skip_or_fail(reason: str):
    if os.environ.get("HIL_REQUIRED") == "1":
        pytest.fail(f"{reason} and HIL_REQUIRED=1")
    pytest.skip(reason)


def open_esp():
    try:
        import serial
    except ImportError:
        skip_or_fail("pyserial not installed")
    if not os.path.exists(PORT):
        skip_or_fail(f"{PORT} not found (no HIL bench)")
    port = serial.Serial()
    port.port = PORT
    port.baudrate = ROM_BAUD
    port.timeout = 0.1
    # Released DTR and RTS = run mode. Asserting one of them alone would hold
    # the ESP in reset or put it in the bootloader.
    port.dtr = False
    port.rts = False
    port.open()
    return port


def test_esp_boots_from_flash_and_stays_up():
    port = open_esp()
    try:
        # Pulse EN through RTS, like esptool's hard reset
        port.reset_input_buffer()
        port.rts = True
        time.sleep(0.1)
        port.rts = False

        rom = read_for(port, ROM_WINDOW_S)
        assert boot_mode(rom) == 3, f"no normal flash boot in ROM output: {rom[:300]!r}"
        assert not crash_markers(rom), f"crash in ROM output: {rom!r}"

        port.baudrate = APP_BAUD
        port.reset_input_buffer()
        after = read_for(port, SILENT_WINDOW_S)
        assert after == b"", (
            f"UART0 should be silent after boot, got {len(after)} bytes "
            f"(markers {crash_markers(after)}): {after[:300]!r}"
        )
    finally:
        port.close()


def test_boot_bytes_never_look_like_msp():
    """ESP-S1-03: what the FC's UART sees at 115200 from the reset edge.

    The ROM banner at 74880 reads as noise at 115200, and Betaflight's MSP
    parser must never find a frame start in it (ADR-012, REQ-SAF-016). With
    the gateway firmware (#101) the ESP sends MSP_API_VERSION requests at
    boot on purpose: this test is then replaced by a check of the BOOT and
    NO_SESSION output (#106).
    """
    port = open_esp()
    try:
        port.baudrate = APP_BAUD
        for reset in range(BOOT_RESETS):
            port.reset_input_buffer()
            port.rts = True
            time.sleep(0.1)
            port.rts = False
            boot = read_for(port, BOOT_WINDOW_S)
            assert boot, f"reset {reset}: no boot output at all, did the reset work?"
            assert msp_like(boot) == [], f"reset {reset}: MSP in boot output: {boot!r}"
    finally:
        port.close()
