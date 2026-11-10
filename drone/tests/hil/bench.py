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
# sd_protocol (the ground side of the protocol) for the session tests
sys.path.insert(0, str(REPO / "drone/ground/src/sd_protocol_py"))
sys.path.insert(0, str(REPO / "scripts"))  # ground_link.py, the bench ground
from flight_interface import msp  # noqa: E402  (same MSP code as scripts/msp_check.py)
from sd_protocol.setpoint import Setpoint, setpoint_to_rc  # noqa: E402

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


# MSP_SET_ARMING_DISABLED (ADR-024): the gateway sends value 0 (release) once
# at every boot, retried until acknowledged; value 1 (lock) only during an OTA
# upload, which no HIL test does. Not in flight_interface.msp: ESP-only.
ARMING_DISABLED = 99
RELEASE = b"\x00"

FAKE_ANSWERS = {
    msp.MSP_ATTITUDE: struct.pack("<hhH", FAKE_ROLL, FAKE_PITCH, FAKE_HEADING),
    # vbat (u8, 0.1 V), mAh drawn, RSSI, amperage, voltage (u16, 0.01 V) at 7
    msp.MSP_ANALOG: struct.pack("<BHHhH", FAKE_VBAT_CV // 10, 0, 0, 0, FAKE_VBAT_CV),
    msp.MSP_STATUS: fake_status(),
    # Betaflight 4.5.1: MSP protocol 0, API 1.46. The gateway stays in BOOT
    # until this is answered (REQ-SAF-016).
    msp.MSP_API_VERSION: bytes([0, 1, 46]),
    msp.MSP_SET_RAW_RC: b"",  # Betaflight acknowledges with an empty reply
    ARMING_DISABLED: b"",  # the same; acknowledged, the boot release is not retried
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
        self.error = None
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
        self.check()

    def check(self):
        """Raise what killed the thread, so a dead fake FC is not read as a
        silent gateway."""
        if self.error is not None:
            raise RuntimeError("fake FC thread died") from self.error

    def clear(self):
        with self._lock:
            self.raw.clear()
            self.frames.clear()
            self.parser = msp.Parser()

    def snapshot(self):
        self.check()
        with self._lock:
            return bytes(self.raw), list(self.frames), self.parser.bad_checksums

    def _run(self):
        try:
            self._serve()
        except Exception as exc:  # re-raised in the test's thread by check()
            self.error = exc

    def _serve(self):
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


# The longest frame the gateway sends is MSP_SET_RAW_RC: 6 + 8 channels x 2 =
# 22 bytes, so a frame cut at either end of a recording leaves at most 21.
MAX_CUT = 21


def stray_bytes(raw: bytes, frames) -> int:
    """Bytes in `raw` that are not the parsed frames back to back.

    The recording can start and end inside a frame, so up to MAX_CUT bytes
    at each end don't count. Any byte between two frames does: text, a crash
    dump, a frame with a bad checksum.
    """
    stream = b"".join(msp.encode_request(f.cmd, f.payload) for _, f in frames)
    start = raw.find(stream)
    if start < 0:
        return len(raw)
    end = len(raw) - start - len(stream)
    return max(start - MAX_CUT, 0) + max(end - MAX_CUT, 0)


# What the gateway may send to the FC (ADR-009), and the only RC it may send
# without an armed session: centred sticks, throttle low.
POLLS = {msp.MSP_STATUS, msp.MSP_ATTITUDE, msp.MSP_ANALOG, msp.MSP_API_VERSION}
RC = msp.MSP_SET_RAW_RC
NEUTRAL = msp.encode_rc(setpoint_to_rc(Setpoint(0, 0, 0, 0, 0)))


def check_frames(raw, frames, bad, rc_ok=(NEUTRAL,)):
    """Only allow-listed requests, nothing outside MSP frames, and every RC
    one of `rc_ok`: neutral, unless the test armed a session and says which
    values it sent. MSP_SET_ARMING_DISABLED only as the release (value 0):
    the lock (value 1) belongs to an OTA upload, never to these tests."""
    assert not crash_markers(raw), f"crash on UART0: {raw[:300]!r}"
    assert bad == 0, f"{bad} frames with a bad checksum"
    assert frames, "the gateway sent nothing: is esp_gateway flashed?"
    assert {f.direction for _, f in frames} == {"<"}, "the gateway only sends requests"
    stray = stray_bytes(raw, frames)
    assert stray == 0, f"{stray} bytes outside MSP frames: {raw[:300]!r}"
    sent = {f.cmd for _, f in frames}
    allowed = POLLS | {RC, ARMING_DISABLED}
    assert sent <= allowed, f"commands outside the allow-list: {sent - allowed}"
    locks = [f.payload for _, f in frames if f.cmd == ARMING_DISABLED]
    assert all(
        p == RELEASE for p in locks
    ), f"MSP_SET_ARMING_DISABLED other than the release (value 0): {locks}"
    assert all(
        f.payload == b"" for _, f in frames if f.cmd in POLLS
    ), "polls carry no payload"
    unexpected = [
        f.payload for _, f in frames if f.cmd == RC and f.payload not in rc_ok
    ]
    assert (
        not unexpected
    ), f"RC the ground never sent, or before ARM: {unexpected[0].hex()}"
