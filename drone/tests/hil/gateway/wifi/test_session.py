"""HIL Wi-Fi: a real Protocol Spec v0 session with the gateway (#102).

Runs only inside softap_window.sh, after hil.yaml flashed the gateway (env
esp). The Pi plays the FC on USB (bench.FakeFc) and the ground over Wi-Fi,
with the same protocol code as scripts/ground_link.py and the HIL PSK
(SEAME_PSK).

The first tests never arm, so the session stays in SESSION_IDLE, where nothing
from the ground may reach the FC: every RC on the UART must stay neutral. The
last three send ARM_CONTROL_REQUEST themselves, under the bench-only exception
to REQ-SAF-023 (Requirements.md, 2026-11-10, #106): passthrough, the ADR-004
link-loss failsafe, and resume.
"""

import os
import socket
import time

import pytest
from bench import (
    APP_BAUD,
    FAKE_ARMING_FLAGS,
    FAKE_HEADING,
    FAKE_PITCH,
    FAKE_ROLL,
    FAKE_VBAT_CV,
    NEUTRAL,
    RC,
    FakeFc,
    Setpoint,
    check_frames,
    msp,
    open_esp,
    setpoint_to_rc,
    skip_or_fail,
)
from ground_link import GroundLink, TcpReader, take_ack

from sd_protocol.frame import MsgType
from sd_protocol.messages import (
    AckResult,
    EspState,
    EventCode,
    FcHealth,
    decode_event,
    decode_u8_payload,
)
from sd_protocol.receiver import is_accepted

ESP = (os.environ.get("HIL_ESP_IP", "192.168.4.1"), 5759)
OPEN_DEADLINE_S = 15.0  # covers an ESP reset when the serial port opens
TICK_S = 0.02  # SETPOINT at 50 Hz, like the ground node
FLOW_NO_DATA = 4  # no flow sensor: the gateway never claims zero motion
OVERRIDE_MASK = 8  # kOverrideMask in src/gateway/main.cpp (yaw, ADR-010)
# sd/link.hpp: setpoint older than T_fresh -> neutral; no control for T_loss
# -> LINK_FAILSAFE; the 2026.6 output is neutral for 1 s, then nothing
T_FRESH_S, T_LOSS_S, FAILSAFE_NEUTRAL_S = 0.150, 0.250, 1.0
# Wi-Fi one way plus one 20 ms RC slot, on top of the firmware's own times
LATE_S = 0.09


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


class Ground:
    """One ground session: TCP for HELLO and control messages, UDP for
    SETPOINT and STATE. Keeps every accepted TCP frame with its arrival time."""

    def __init__(self, key: bytes):
        self.link = GroundLink(key)
        self.tcp = self._open()
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.settimeout(TICK_S)
        self.tcp_rx = []  # (time.monotonic(), msg type, payload)
        self.health = []  # FC_HEALTH received (spec v1.4, #179)
        self.last_sent = None  # time of the last SETPOINT

    def _open(self):
        deadline = time.monotonic() + OPEN_DEADLINE_S
        while time.monotonic() < deadline:
            try:
                tcp = socket.create_connection(ESP, timeout=2)
            except OSError:  # not associated yet, or the ESP restarting
                time.sleep(0.5)
                continue
            try:
                tcp.sendall(self.link.hello())
                self.reader = TcpReader()
                while True:
                    data = tcp.recv(512)
                    if not data:
                        break
                    if take_ack(self.link, self.reader.feed(data)) is not None:
                        tcp.setblocking(False)
                        return tcp
            except OSError:
                pass
            tcp.close()
            time.sleep(0.6)  # HELLO is rate-limited to one per 500 ms
        return None

    def close(self):
        self.udp.close()
        if self.tcp:
            self.tcp.close()

    def poll_tcp(self):
        try:
            data = self.tcp.recv(4096)
        except OSError:  # nothing yet, or closed: the asserts say what's missing
            return
        for frame in self.reader.feed(data):
            result, f = self.link.rx_tcp.receive(frame)
            if is_accepted(result) and f is not None:
                self.tcp_rx.append((time.monotonic(), f.header.type, f.payload))

    def send_tcp(self, frame: bytes):
        self.tcp.sendall(frame)  # one small frame: fits the socket buffer

    def stream(self, seconds, mode=0, send=True, each=None, **axes):
        """SETPOINT every 20 ms for `seconds` (none if `send` is False: a link
        loss), reading TCP and calling `each()` every tick. Returns the STATEs
        received (STATE goes to the last accepted sender)."""
        states = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            tick = time.monotonic() + TICK_S
            if send:
                self.udp.sendto(self.link.setpoint(mode, **axes), ESP)
                self.last_sent = time.monotonic()
            self.poll_tcp()
            if each:
                each()
            while time.monotonic() < tick:
                try:
                    st = self.link.telemetry(self.udp.recvfrom(512)[0])
                except OSError:
                    continue
                if isinstance(st, FcHealth):
                    self.health.append(st)
                elif st is not None:
                    states.append(st)
        return states

    def reply(self, msg_type, since: float):
        """The u8 result of the first `msg_type` received after `since`."""
        for t, typ, payload in self.tcp_rx:
            if typ == msg_type and t >= since:
                return decode_u8_payload(payload)
        return None

    def events(self, code, since: float):
        return [
            t
            for t, typ, payload in self.tcp_rx
            if typ == MsgType.EVENT
            and t >= since
            and decode_event(payload).code == code
        ]


@pytest.fixture(scope="module")
def session(fake_fc):
    ground = Ground(psk())
    fake_fc.check()
    assert (
        ground.tcp is not None
    ), "no HELLO_ACK: wrong PSK, or not the gateway firmware?"
    yield ground
    ground.close()


def test_state_carries_the_fake_fc_values(fake_fc, session):
    session.stream(1.0)  # the ESP learns where to send STATE
    fake_fc.clear()
    states = session.stream(3.0)
    assert len(states) >= 50, f"{len(states)} STATE in 3 s (25 Hz expected)"
    first, last = states[0], states[-1]
    assert last.esp_state == EspState.SESSION_IDLE
    assert (last.roll, last.pitch, last.heading) == (
        FAKE_ROLL,
        FAKE_PITCH,
        FAKE_HEADING,
    )
    assert last.vbat == FAKE_VBAT_CV
    assert last.flow_status == FLOW_NO_DATA
    assert last.last_ctrl_seq > first.last_ctrl_seq, "SETPOINTs not accepted"
    assert last.rx_rejected == first.rx_rejected, "valid frames were rejected"
    assert last.msp_errors == first.msp_errors, "MSP errors with a healthy FC"
    check_frames(*fake_fc.snapshot())


def test_setpoints_without_arm_never_reach_the_fc(fake_fc, session):
    """SESSION_IDLE: a mode 1 setpoint with roll 500 is accepted, and the FC
    still only gets neutral RC (only ARM_CONTROL_ACK OK starts control)."""
    fake_fc.clear()
    states = session.stream(2.0, mode=1, roll=500)
    assert states and states[-1].esp_state == EspState.SESSION_IDLE
    assert states[-1].last_ctrl_seq > states[0].last_ctrl_seq, "SETPOINTs not accepted"
    check_frames(*fake_fc.snapshot())  # every RC neutral


def test_garbage_is_rejected_and_never_reaches_the_fc(fake_fc, session):
    before = session.stream(0.5)[-1].rx_rejected
    fake_fc.clear()
    garbage = session.link.garbage()  # bad tag, replay, old seq, bad mode, random
    for _, frame in garbage:
        session.udp.sendto(frame, ESP)
    states = session.stream(1.0)
    # The bad mode passes the tag check, so it may not count as a reject
    assert states[-1].rx_rejected - before >= len(garbage) - 1, [n for n, _ in garbage]
    assert states[-1].esp_state == EspState.SESSION_IDLE
    check_frames(*fake_fc.snapshot())


def test_wrong_psk_hello_gets_no_ack_and_the_session_keeps_running(fake_fc, session):
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
    states = session.stream(2.5, each=read_intruder)
    tcp.close()
    assert not acked, "HELLO_ACK for a HELLO signed with another PSK"
    assert len(states) >= 40, "the session stopped sending STATE"
    assert states[-1].esp_state == EspState.SESSION_IDLE
    check_frames(*fake_fc.snapshot())


# --- FC link health (#179, spec v1.4). No ARM here: the bench exception to
# REQ-SAF-023 covers only the three armed tests below; the ARM refusal while
# the FC is silent (result 4) is unit-tested (test_link.cpp).


def test_fc_health_carries_the_fake_fc_link(fake_fc, session):
    session.health.clear()
    session.stream(1.2)
    assert (
        4 <= len(session.health) <= 9
    ), f"{len(session.health)} FC_HEALTH in 1.2 s (5 Hz)"
    h = session.health[-1]
    assert h.fc_alive == 1
    assert (h.armed, h.arming_disable_flags) == (0, FAKE_ARMING_FLAGS)
    assert h.status_age_ms <= 400 and h.attitude_age_ms <= 120  # 5 Hz / 25 Hz polls
    assert h.reply_age_ms <= 100, "a healthy FC answers every ~12 ms"
    assert h.replies > 0 and h.rc_sent > 0


def test_a_silent_fc_is_reported_within_500_ms(fake_fc, session):
    """ESP-S2-06 on the bench: the fake FC stops answering. FC_LINK_LOST
    must reach the ground within 500 ms (250 ms on the ESP plus Wi-Fi), then
    FC_LINK_RESTORED once it answers again; the session itself goes on."""
    session.stream(0.3)
    t = time.monotonic()
    fake_fc.answering = False
    try:
        states = session.stream(0.9)
    finally:
        fake_fc.answering = True
    lost = session.events(EventCode.FC_LINK_LOST, t)
    assert lost, "no FC_LINK_LOST"
    assert (
        lost[0] - t <= 0.5
    ), f"FC_LINK_LOST {lost[0] - t:.3f} s after the FC went silent"
    assert session.health[-1].fc_alive == 0
    assert (
        session.health[-1].armed == 0xFF
    ), "armed must be unknown with no fresh status"
    assert states[-1].esp_state == EspState.SESSION_IDLE  # D2: the state is kept
    t2 = time.monotonic()
    session.stream(0.8)
    assert session.events(EventCode.FC_LINK_RESTORED, t2), "no FC_LINK_RESTORED"
    assert session.health[-1].fc_alive == 1


# --- Armed tests: bench-only exception to REQ-SAF-023 (Requirements.md,
# 2026-11-10, #106). Each opens its own session, so none depends on another.

PASS_SP = dict(roll=1500, pitch=-400, throttle=200, yaw=300)  # roll over the limit
PASS_RC = msp.encode_rc(setpoint_to_rc(Setpoint(1, 1500, -400, 300, 200)))
LOSS_RC = msp.encode_rc(setpoint_to_rc(Setpoint(1, 500, 0, 0, 0)))


@pytest.fixture
def armed(fake_fc):
    """A new session, armed with the gateway's override mask: STREAMING."""
    ground = Ground(psk())
    assert ground.tcp is not None, "no HELLO_ACK for the armed session"
    ground.stream(0.5)  # confirms the session (first UDP frame) and the peer
    t = time.monotonic()
    # ARM from a test: HIL bench exception to REQ-SAF-023 (Requirements.md, 2026-11-10)
    ground.send_tcp(ground.link.arm(OVERRIDE_MASK))
    states = ground.stream(0.3)  # mode 0 keeps control alive after ARM
    assert ground.reply(MsgType.ARM_CONTROL_ACK, t) == AckResult.OK
    assert states[-1].esp_state == EspState.STREAMING
    yield ground
    ground.close()


def test_host_expected_rc_values():
    """Host check (no bench): what the armed tests expect on the UART."""
    # AETR order; roll clamped to 1000 -> 2000 us; throttle 200 -> 1200 us
    assert setpoint_to_rc(Setpoint(1, 1500, -400, 300, 200))[:4] == [
        2000,
        1300,
        1200,
        1650,
    ]
    assert PASS_RC != NEUTRAL and LOSS_RC != NEUTRAL


def test_armed_setpoint_reaches_the_fc_clamped(fake_fc, armed):
    """Passthrough: after ARM OK, a mode 1 SETPOINT becomes MSP_SET_RAW_RC in
    AETR order, clamped to the setpoint limit. The ESP sends all four sticks;
    the FC applies the override mask (ADR-010)."""
    # Armed by the `armed` fixture: HIL bench exception to REQ-SAF-023
    # (Requirements.md, 2026-11-10, #106)
    fake_fc.clear()
    states = armed.stream(1.0, mode=1, **PASS_SP)
    raw, frames, bad = fake_fc.snapshot()
    check_frames(raw, frames, bad, rc_ok=(NEUTRAL, PASS_RC))
    rc = [f.payload for _, f in frames if f.cmd == RC]
    # ~50 slots in 1 s; the first ones are neutral until the first setpoint
    assert (
        rc.count(PASS_RC) >= 40
    ), f"{rc.count(PASS_RC)} of {len(rc)} RC carried the setpoint"
    assert states[-1].esp_state == EspState.STREAMING


def test_link_loss_failsafe_within_250_ms(fake_fc, armed):
    """Stop SETPOINT in STREAMING. On the UART: ground values until T_fresh
    (150 ms), then neutral; LINK_FAILSAFE_ENTERED at T_loss (250 ms); the
    neutral stops 1 s after that (2026.6 output, ADR-004)."""
    # Armed by the `armed` fixture: HIL bench exception to REQ-SAF-023
    # (Requirements.md, 2026-11-10, #106)
    armed.stream(0.5, mode=1, roll=500)
    fake_fc.clear()
    armed.stream(0.3, mode=1, roll=500)
    t_last = armed.last_sent
    states = armed.stream(2.0, send=False)  # the link loss
    raw, frames, bad = fake_fc.snapshot()
    check_frames(raw, frames, bad, rc_ok=(NEUTRAL, LOSS_RC))
    after = [(t - t_last, f.payload) for t, f in frames if f.cmd == RC and t > t_last]
    first_neutral = next(dt for dt, p in after if p == NEUTRAL)
    assert T_FRESH_S - 0.005 <= first_neutral <= T_FRESH_S + LATE_S, first_neutral
    assert all(
        p == NEUTRAL for dt, p in after if dt >= first_neutral
    ), "values after neutral"
    last_rc = after[-1][0]
    stop = T_LOSS_S + FAILSAFE_NEUTRAL_S
    assert (
        stop - 0.03 <= last_rc <= stop + LATE_S
    ), f"RC stopped {last_rc:.3f} s after the loss"
    entered = armed.events(EventCode.LINK_FAILSAFE_ENTERED, t_last)
    assert entered, "no LINK_FAILSAFE_ENTERED"
    # The ESP times T_loss from the last setpoint's arrival: Wi-Fi both ways on top
    detected = entered[0] - t_last
    assert (
        T_LOSS_S - 0.005 <= detected <= T_LOSS_S + 2 * LATE_S
    ), f"detected at {detected:.3f} s"
    assert states[-1].esp_state == EspState.LINK_FAILSAFE


def test_resume_only_through_the_handshake(fake_fc, armed):
    """After LINK_FAILSAFE, SETPOINTs alone never bring ground values back,
    and RESUME_REQUEST needs 500 ms of neutral first (REQ-COM-020)."""
    # Armed by the `armed` fixture: HIL bench exception to REQ-SAF-023
    # (Requirements.md, 2026-11-10, #106)
    armed.stream(0.5, mode=1, roll=500)
    armed.stream(0.6, send=False)  # loss -> LINK_FAILSAFE
    fake_fc.clear()
    states = armed.stream(1.0, mode=1, roll=500)
    assert (
        states[-1].esp_state == EspState.LINK_FAILSAFE
    ), "setpoints ended the failsafe"
    t = time.monotonic()
    armed.send_tcp(armed.link.tcp(MsgType.RESUME_REQUEST))
    armed.stream(0.7)  # neutral, mode 0: the 500 ms a resume needs
    assert armed.reply(MsgType.RESUME_ACK, t) == AckResult.RESUME_CONDITION_NOT_MET
    t = time.monotonic()
    armed.send_tcp(armed.link.tcp(MsgType.RESUME_REQUEST))
    states = armed.stream(0.3)
    assert armed.reply(MsgType.RESUME_ACK, t) == AckResult.OK
    assert states[-1].esp_state == EspState.STREAMING
    raw, frames, bad = fake_fc.snapshot()
    check_frames(raw, frames, bad)  # no ground value reached the FC until now
    fake_fc.clear()
    armed.stream(0.5, mode=1, roll=500)
    rc = [f.payload for _, f in fake_fc.snapshot()[1] if f.cmd == RC]
    assert rc.count(LOSS_RC) >= 15, "ground values did not come back after RESUME OK"
