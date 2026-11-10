"""HIL Wi-Fi: a real Protocol Spec v0 session with the gateway (#102).

Runs only inside softap_window.sh, after hil.yaml flashed esp_gateway. The Pi
plays the FC on USB (bench.FakeFc) and the ground over Wi-Fi, with the same
protocol code as scripts/ground_link.py and the HIL PSK (SEAME_PSK).

The tests never send ARM_CONTROL_REQUEST (REQ-SAF-023: ARM is an operator
action), so the session stays in SESSION_IDLE, where nothing from the ground
may reach the FC: every RC on the UART must stay neutral. The tests that need
STREAMING are at the end, skipped until the safety pilot decides (#106).
"""

import os
import socket
import time

import pytest
from bench import (
    APP_BAUD,
    FAKE_HEADING,
    FAKE_PITCH,
    FAKE_ROLL,
    FAKE_VBAT_CV,
    FakeFc,
    check_frames,
    open_esp,
    skip_or_fail,
)
from ground_link import GroundLink, TcpReader, take_ack
from sd_protocol.messages import EspState

ESP = (os.environ.get("HIL_ESP_IP", "192.168.4.1"), 5759)
OPEN_DEADLINE_S = 15.0  # covers an ESP reset when the serial port opens
TICK_S = 0.02  # SETPOINT at 50 Hz, like the ground node
FLOW_NO_DATA = 4  # no flow sensor: the gateway never claims zero motion


def psk() -> bytes:
    text = os.environ.get("SEAME_PSK", "")
    if len(text) != 64:
        skip_or_fail("SEAME_PSK (64 hex characters) not set")
    return bytes.fromhex(text)


@pytest.fixture(scope="module")
def fake_fc():
    port = open_esp(APP_BAUD)
    try:
        with FakeFc(port) as fc:
            yield fc
    finally:
        port.close()


def open_session(key: bytes):
    """HELLO / HELLO_ACK over TCP. Returns (link, tcp), or (link, None)."""
    link = GroundLink(key)
    deadline = time.monotonic() + OPEN_DEADLINE_S
    while time.monotonic() < deadline:
        try:
            tcp = socket.create_connection(ESP, timeout=2)
        except OSError:  # not associated yet, or the ESP restarting
            time.sleep(0.5)
            continue
        try:
            tcp.sendall(link.hello())
            reader = TcpReader()
            while True:
                data = tcp.recv(512)
                if not data:
                    break
                if take_ack(link, reader.feed(data)) is not None:
                    return link, tcp
        except OSError:
            pass
        tcp.close()
        time.sleep(0.6)  # HELLO is rate-limited to one per 500 ms
    return link, None


@pytest.fixture(scope="module")
def session(fake_fc):
    link, tcp = open_session(psk())
    fake_fc.check()
    assert tcp is not None, "no HELLO_ACK: wrong PSK, or not the gateway firmware?"
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.settimeout(TICK_S)
    yield link, udp
    udp.close()
    tcp.close()


def stream(link, udp, seconds, roll=0, mode=0, each=None):
    """Sends SETPOINT every 20 ms for `seconds`, calling `each()` every tick.
    Returns the STATEs received (STATE goes to the last accepted sender)."""
    states = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        tick = time.monotonic() + TICK_S
        udp.sendto(link.setpoint(mode, roll=roll), ESP)
        if each:
            each()
        while time.monotonic() < tick:
            try:
                st = link.state(udp.recvfrom(512)[0])
            except OSError:
                continue
            if st is not None:
                states.append(st)
    return states


def test_state_carries_the_fake_fc_values(fake_fc, session):
    link, udp = session
    states = stream(link, udp, 1.0)  # the ESP learns where to send STATE
    fake_fc.clear()
    states = stream(link, udp, 3.0)
    assert len(states) >= 50, f"{len(states)} STATE in 3 s (25 Hz expected)"
    first, last = states[0], states[-1]
    assert last.esp_state == EspState.SESSION_IDLE
    assert (last.roll, last.pitch, last.heading) == (FAKE_ROLL, FAKE_PITCH, FAKE_HEADING)
    assert last.vbat == FAKE_VBAT_CV
    assert last.flow_status == FLOW_NO_DATA
    assert last.last_ctrl_seq > first.last_ctrl_seq, "SETPOINTs not accepted"
    assert last.rx_rejected == first.rx_rejected, "valid frames were rejected"
    assert last.msp_errors == first.msp_errors, "MSP errors with a healthy FC"
    check_frames(*fake_fc.snapshot())


def test_setpoints_without_arm_never_reach_the_fc(fake_fc, session):
    """SESSION_IDLE: a mode 1 setpoint with roll 500 is accepted, and the FC
    still only gets neutral RC (only ARM_CONTROL_ACK OK starts control)."""
    link, udp = session
    fake_fc.clear()
    states = stream(link, udp, 2.0, roll=500, mode=1)
    assert states and states[-1].esp_state == EspState.SESSION_IDLE
    assert states[-1].last_ctrl_seq > states[0].last_ctrl_seq, "SETPOINTs not accepted"
    check_frames(*fake_fc.snapshot())  # every RC neutral


def test_garbage_is_rejected_and_never_reaches_the_fc(fake_fc, session):
    link, udp = session
    before = stream(link, udp, 0.5)[-1].rx_rejected
    fake_fc.clear()
    garbage = link.garbage()  # bad tag, replay, old seq, bad mode, random bytes
    for _, frame in garbage:
        udp.sendto(frame, ESP)
    states = stream(link, udp, 1.0)
    # The bad mode passes the tag check, so it may not count as a reject
    assert states[-1].rx_rejected - before >= len(garbage) - 1, [n for n, _ in garbage]
    assert states[-1].esp_state == EspState.SESSION_IDLE
    check_frames(*fake_fc.snapshot())


def test_wrong_psk_hello_gets_no_ack_and_the_session_keeps_running(fake_fc, session):
    link, udp = session
    intruder = GroundLink(os.urandom(32))
    tcp = socket.create_connection(ESP, timeout=2)
    tcp.setblocking(False)
    tcp.sendall(intruder.hello())
    reader, acked = TcpReader(), []

    def read_intruder():
        try:
            data = tcp.recv(512)
        except BlockingIOError:
            return
        if data and take_ack(intruder, reader.feed(data)) is not None:
            acked.append(True)

    fake_fc.clear()
    # The ESP closes a connection with no session after 2 s (ADR-005)
    states = stream(link, udp, 2.5, each=read_intruder)
    tcp.close()
    assert not acked, "HELLO_ACK for a HELLO signed with another PSK"
    assert len(states) >= 40, "the session stopped sending STATE"
    assert states[-1].esp_state == EspState.SESSION_IDLE
    check_frames(*fake_fc.snapshot())


ARM_DECISION = (
    "needs ARM_CONTROL_REQUEST from an automated test: waiting for the "
    "safety pilot's decision on REQ-SAF-023 for the HIL bench (#106)"
)


@pytest.mark.skip(reason=ARM_DECISION)
def test_armed_setpoint_reaches_the_fc_clamped():
    """Passthrough: after ARM OK, a mode 1 SETPOINT becomes MSP_SET_RAW_RC in
    AETR order, clamped, on the overridden axes only (mask 8, yaw)."""


@pytest.mark.skip(reason=ARM_DECISION)
def test_link_loss_failsafe_within_250_ms():
    """Stop SETPOINT in STREAMING: the first neutral RC on the UART comes
    230-290 ms after the last ground value (ADR-004), then RC stops after 1 s
    (2026.6 output)."""


@pytest.mark.skip(reason=ARM_DECISION)
def test_resume_only_through_the_handshake():
    """After LINK_FAILSAFE, SETPOINTs alone never bring ground values back:
    only RESUME_REQUEST after 500 ms of neutral does."""
