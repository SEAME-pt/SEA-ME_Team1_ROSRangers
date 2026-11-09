"""Shared HIL bench helpers: the spare ESP's serial port and a fake FC on it.

The spare ESP sits on the Pi's USB (/dev/hil-esp, FTDI FT232R, auto-reset on
DTR/RTS). There is no FC on the bench (#34), so for the gateway firmware the
Pi plays the FC on that port: `FakeFc` answers the gateway's MSP polls and
records every byte the ESP sends, as the FC's UART would see it.

Without the board the hardware tests are skipped, unless HIL_REQUIRED=1 (set
in hil.yaml) turns the skip into a failure.
"""

import os
import pathlib
import re
import struct
import sys
import threading
import time
import warnings

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "drone/ground/src/flight_interface"))
from flight_interface import msp  # noqa: E402  (same MSP code as scripts/msp_check.py)

PORT = os.environ.get("HIL_ESP_PORT", "/dev/hil-esp")
ROM_BAUD = 74880
APP_BAUD = 115200

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


def read_for(port, seconds: float) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        data += port.read(port.in_waiting or 1)
    return bytes(data)


def skip_or_fail(reason: str):
    if os.environ.get("HIL_REQUIRED") == "1":
        pytest.fail(f"{reason} and HIL_REQUIRED=1")
    pytest.skip(reason)


def open_esp(baud: int = ROM_BAUD):
    """Open the spare ESP's port in run mode. Opening it may reset the ESP,
    so open it once per module, not once per test."""
    try:
        import serial
    except ImportError:
        skip_or_fail("pyserial not installed")
    if not os.path.exists(PORT):
        skip_or_fail(f"{PORT} not found (no HIL bench)")
    port = serial.Serial()
    port.port = PORT
    port.baudrate = baud
    port.timeout = 0.1
    # Released DTR and RTS = run mode. Asserting one of them alone would hold
    # the ESP in reset or put it in the bootloader.
    port.dtr = False
    port.rts = False
    port.open()
    # FTDI latency timer 16 ms -> 1 ms, for the failsafe-timing tests (#102).
    # A warning, not a failure, until a test depends on it.
    try:
        port.set_low_latency_mode(True)
    except (OSError, ValueError, AttributeError) as exc:
        warnings.warn(f"low-latency mode not set on {PORT}: {exc}", stacklevel=2)
    return port


def pulse_reset(port):
    """Pulse EN through RTS, like esptool's hard reset."""
    port.reset_input_buffer()
    port.rts = True
    time.sleep(0.1)
    port.rts = False


# Replies the gateway's decoders accept (drone/lib/protocol/src/msp.cpp), with
# values no default or zeroed field could produce, so a test can tell the
# fake FC's answers apart from "nothing received".
FAKE_ROLL, FAKE_PITCH, FAKE_HEADING = 123, -45, 270  # 0.1 deg, 0.1 deg, deg
FAKE_VBAT_CV = 1111  # 11.11 V
FAKE_ARMING_FLAGS = 0x00000020


def fake_status(armed: bool = False) -> bytes:
    # cycle, i2c errors, sensors, flight mode flags, profile, load, gyro cycle,
    # 0 extra flag bytes, arming-disable flag count, arming-disable flags,
    # config state
    head = struct.pack("<HHHIBHH", 125, 0, 0x23, int(armed), 0, 5, 125)
    return head + bytes([0, 26]) + struct.pack("<I", FAKE_ARMING_FLAGS) + bytes([0])


FAKE_ANSWERS = {
    msp.MSP_ATTITUDE: struct.pack("<hhH", FAKE_ROLL, FAKE_PITCH, FAKE_HEADING),
    # vbat (u8, 0.1 V), mAh drawn, RSSI, amperage, voltage (u16, 0.01 V) at 7
    msp.MSP_ANALOG: struct.pack("<BHHhH", FAKE_VBAT_CV // 10, 0, 0, 0, FAKE_VBAT_CV),
    msp.MSP_STATUS: fake_status(),
}


class FakeFc:
    """Plays the FC on the ESP's UART in a background thread.

    Records every byte the ESP sends (`raw`) and every MSP frame in it
    (`frames`, with the time it was parsed). Answers the polls in
    FAKE_ANSWERS while `answering` is set; anything else gets no answer.
    """

    def __init__(self, port):
        self.port = port
        self.answering = True
        self.raw = bytearray()
        self.frames = []  # (time.monotonic(), msp.Frame)
        self.parser = msp.Parser()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.port.reset_input_buffer()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)

    def clear(self):
        with self._lock:
            self.raw.clear()
            self.frames.clear()
            self.parser = msp.Parser()

    def snapshot(self):
        with self._lock:
            return bytes(self.raw), list(self.frames), self.parser.bad_checksums

    def _run(self):
        while not self._stop.is_set():
            data = self.port.read(self.port.in_waiting or 1)
            if not data:
                continue
            now = time.monotonic()
            with self._lock:
                self.raw += data
                frames = self.parser.feed(data)
                self.frames += [(now, f) for f in frames]
            for f in frames:
                if self.answering and f.direction == "<" and f.cmd in FAKE_ANSWERS:
                    self.port.write(msp.encode_response(f.cmd, FAKE_ANSWERS[f.cmd]))


def frame_bytes(frames) -> int:
    """Wire size of the parsed frames: '$M<', size, cmd, payload, checksum."""
    return sum(6 + len(f.payload) for _, f in frames)
