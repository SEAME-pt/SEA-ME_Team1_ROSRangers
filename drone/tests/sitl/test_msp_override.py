"""MSP Override and MSP_RC readback against Betaflight 2026.6.2 SITL (#105).

CI counterpart of ESP_to_FC §11 Phases 3 and 4. The test plays both sides:
the pilot's radio streams RC over UDP 9004 (SITL's receiver) and the ESP
sends MSP_SET_RAW_RC on UART1. override.cli sets the mask and the switches.
"""

import socket
import struct
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import HOST, skip_or_fail
from msp_frames import FROM_FC

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "drone/ground/src/flight_interface/flight_interface"))
from flight_interface import msp as codec  # noqa: E402  (the ground node's MSP encoder)

MSP_MODE_RANGES = 34
MSP_BOXIDS = 119
BOX_MSP_OVERRIDE = 50
RC_PORT = 9004

# Raw channel index (AETR1234), as in override.cli
ROLL, PITCH, THROTTLE, YAW, ARM, AUX2, AUX3, OVERRIDE = range(8)
LOW, HIGH = 1000, 2000

# Throttle low, so the FC could arm if ARM leaked through
PILOT = [1400, 1450, LOW, 1550, LOW, LOW, LOW, LOW]
# Every frame carries all 8 channels (ADR-010 condition 2). Every channel the
# ESP must not control differs from the pilot's, so a leak shows.
ESP = [1600, 1650, 1700, 1750, HIGH, 1800, 1800, LOW]
NOT_ESP = (THROTTLE, ARM, AUX2, AUX3, OVERRIDE)


class Radio(threading.Thread):
    """The pilot's radio: rc_packet (double timestamp + 16 x uint16) at 50 Hz."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.channels = PILOT + [LOW] * 8
        self.running = True
        self.error = None

    def run(self):
        try:
            while self.running:
                packet = struct.pack("<d16H", time.monotonic(), *self.channels)
                self.sock.sendto(packet, (HOST, RC_PORT))
                time.sleep(0.02)
        except Exception as e:  # reported by read_rc, not lost with the thread
            self.error = e

    def stop(self):
        self.running = False
        self.join()
        self.sock.close()


def request(link, cmd, payload=b""):
    link.send(codec.encode_request(cmd, payload))
    while (frame := link.read_frame()) is not None:
        if frame.cmd == cmd:
            assert frame.direction == FROM_FC, f"MSP {cmd}: error reply"
            return frame.payload
    pytest.fail(f"no reply to MSP {cmd}")


def read_rc(link, radio, channels):
    """One ESP frame, then MSP_RC on channels, in the raw AETR order."""
    assert radio.error is None, f"radio thread died: {radio.error!r}"
    request(link, codec.MSP_SET_RAW_RC, codec.encode_rc(ESP))
    payload = request(link, codec.MSP_RC)
    values = struct.unpack(f"<{len(payload) // 2}H", payload)
    # MSP_RC is roll, pitch, yaw, throttle: swap back to the raw AETR order
    values = values[:2] + values[3:4] + values[2:3] + values[4:]
    return {ch: values[ch] if ch < len(values) else None for ch in channels}


def rc_held(link, radio, want, timeout=2.0, hold=0.5):
    """Waits up to timeout for MSP_RC to match want, then keeps reading for
    hold seconds. Returns the first reading that differs, or want."""
    deadline = time.monotonic() + timeout
    while (got := read_rc(link, radio, want)) != want:
        if time.monotonic() > deadline:
            return got
        time.sleep(0.02)
    end = time.monotonic() + hold
    while time.monotonic() < end:
        if (got := read_rc(link, radio, want)) != want:
            return got
        time.sleep(0.02)
    return want


def arming(link, radio):
    read_rc(link, radio, ())  # keep the ESP streaming
    return codec.decode_status(request(link, codec.MSP_STATUS))


def pick(values, *channels):
    return {ch: values[ch] for ch in channels}


@pytest.fixture(scope="module")
def radio(sitl_session):
    # One radio for the module: SITL never sees RX loss between tests
    ranges = request(sitl_session, MSP_MODE_RANGES)
    # 4 bytes per range: box id, AUX index, start step, end step
    mapped = {(ranges[i], ranges[i + 1]) for i in range(0, len(ranges), 4)}
    if (
        BOX_MSP_OVERRIDE not in request(sitl_session, MSP_BOXIDS)
        or (
            BOX_MSP_OVERRIDE,
            OVERRIDE - 4,
        )
        not in mapped
    ):
        skip_or_fail(
            "SITL misprovisioned: no mask or no MSP OVERRIDE on AUX4, load override.cli"
        )
    radio = Radio()
    radio.start()
    yield radio
    radio.stop()


def switch(radio, override):
    radio.channels[OVERRIDE] = HIGH if override else LOW


def test_override_off_reads_the_pilot(msp, radio):
    # Phase 3: readback path, the ESP streams but the switch is off
    switch(radio, False)
    want = pick(PILOT, *range(8))
    assert rc_held(msp, radio, want) == want


def test_override_on_sets_only_the_masked_axes(msp, radio):
    # Phase 3: roll, pitch and yaw come from the ESP, the rest from the pilot
    switch(radio, True)
    want = pick(ESP, ROLL, PITCH, YAW) | pick(radio.channels, *NOT_ESP)
    assert rc_held(msp, radio, want) == want


def test_arm_and_override_switch_cannot_be_set_over_msp(msp, radio):
    # REQ-SAF-003, REQ-SAF-004: the ESP sends ARM high and the switch low.
    # First wait until nothing else blocks arming (boot grace, ...), so a
    # leaked ARM would really arm: else "not armed" proves nothing.
    switch(radio, False)
    deadline = time.monotonic() + 10
    while (status := arming(msp, radio))["arming_disable_flags"]:
        assert time.monotonic() < deadline, f"SITL cannot arm at all: {status}"
        time.sleep(0.1)
    switch(radio, True)
    want = {ROLL: ESP[ROLL], ARM: LOW, OVERRIDE: HIGH}
    assert rc_held(msp, radio, want) == want
    assert not arming(msp, radio)["armed"]


def test_takeover_returns_every_channel_to_the_pilot(msp, radio):
    # Phase 4: switch off while the ESP keeps streaming
    switch(radio, True)
    on = {ROLL: ESP[ROLL]}
    assert rc_held(msp, radio, on, hold=0) == on
    switch(radio, False)
    want = pick(PILOT, *range(8))
    assert rc_held(msp, radio, want) == want
