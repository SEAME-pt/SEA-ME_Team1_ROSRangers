"""MSP Override and MSP_RC readback against Betaflight 2026.6.2 SITL (#105).

CI counterpart of ESP_to_FC §11 Phases 3 and 4. The test plays both sides:
the pilot's radio streams RC over UDP 9004 (SITL's receiver) and the ESP
sends MSP_SET_RAW_RC on UART1. override.cli sets the mask and the switches.
"""

import socket
import struct
import threading
import time

import pytest
from conftest import HOST, skip_or_fail
from msp_frames import encode_v1

MSP_RC = 105
MSP_BOXIDS = 119
MSP_SET_RAW_RC = 200
BOX_MSP_OVERRIDE = 50
RC_PORT = 9004

# Raw channel index, as in override.cli
ROLL, PITCH, THROTTLE, YAW, ARM, OVERRIDE = 0, 1, 2, 3, 4, 7
LOW, HIGH = 1000, 2000

PILOT = [1400, 1450, 1300, 1550, LOW, LOW, LOW, LOW]
# Every frame carries all 8 channels (ADR-010 condition 2). ARM and the
# override switch are set to what the pilot does NOT have, so a leak shows.
ESP = [1600, 1650, 1700, 1750, HIGH, LOW, LOW, LOW]


class Radio(threading.Thread):
    """The pilot's radio: rc_packet (double timestamp + 16 x uint16) at 50 Hz."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.channels = PILOT + [LOW] * 8
        self.running = True

    def run(self):
        while self.running:
            packet = struct.pack("<d16H", time.monotonic(), *self.channels)
            self.sock.sendto(packet, (HOST, RC_PORT))
            time.sleep(0.02)


def request(msp, cmd, payload=b""):
    msp.send(encode_v1(cmd, payload))
    while (frame := msp.read_frame()) is not None:
        if frame.cmd == cmd:
            return frame.payload
    pytest.fail(f"no reply to MSP {cmd}")


def rc_when(msp, want, esp=None, timeout=1.0):
    """MSP_RC on the channels in want, polled until they match or timeout.
    With esp, sends that frame before each read, as the ESP streams."""
    deadline = time.monotonic() + timeout
    while True:
        if esp:
            request(msp, MSP_SET_RAW_RC, struct.pack("<8H", *esp))
        payload = request(msp, MSP_RC)
        values = struct.unpack(f"<{len(payload) // 2}H", payload)
        # MSP_RC is roll, pitch, yaw, throttle: swap back to the raw AETR order
        values = values[:2] + values[3:4] + values[2:3] + values[4:]
        got = {ch: values[ch] if ch < len(values) else None for ch in want}
        if got == want or time.monotonic() > deadline:
            return got
        time.sleep(0.02)


def pick(values, *channels):
    return {ch: values[ch] for ch in channels}


@pytest.fixture
def radio(msp):
    # MSP OVERRIDE is only in MSP_BOXIDS when the mask is set
    if BOX_MSP_OVERRIDE not in request(msp, MSP_BOXIDS):
        skip_or_fail("MSP OVERRIDE not configured: provision SITL with override.cli")
    radio = Radio()
    radio.start()
    yield radio
    radio.running = False
    radio.join()


def switch(radio, override):
    radio.channels[OVERRIDE] = HIGH if override else LOW


def test_override_off_reads_the_pilot(msp, radio):
    # Phase 3: readback path, the ESP streams but the switch is off
    switch(radio, False)
    want = pick(PILOT, ROLL, PITCH, THROTTLE, YAW, ARM, OVERRIDE)
    assert rc_when(msp, want, esp=ESP) == want


def test_override_on_sets_only_the_masked_axes(msp, radio):
    # Phase 3: roll, pitch and yaw come from the ESP, throttle from the pilot
    switch(radio, True)
    want = pick(ESP, ROLL, PITCH, YAW) | pick(PILOT, THROTTLE)
    assert rc_when(msp, want, esp=ESP) == want


def test_arm_and_override_switch_cannot_be_set_over_msp(msp, radio):
    # REQ-SAF-003, REQ-SAF-004: the ESP sends ARM high and the switch low
    switch(radio, True)
    want = {ROLL: ESP[ROLL], ARM: LOW, OVERRIDE: HIGH}
    assert rc_when(msp, want, esp=ESP) == want


def test_takeover_returns_every_channel_to_the_pilot(msp, radio):
    # Phase 4: switch off while the ESP keeps streaming
    switch(radio, True)
    assert rc_when(msp, {ROLL: ESP[ROLL]}, esp=ESP) == {ROLL: ESP[ROLL]}
    switch(radio, False)
    want = pick(PILOT, ROLL, PITCH, THROTTLE, YAW, ARM, OVERRIDE)
    assert rc_when(msp, want, esp=ESP) == want
