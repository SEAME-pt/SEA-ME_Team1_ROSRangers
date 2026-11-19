"""Tests of esp_backend.py against the fake ESP (test/fake_esp.py) on localhost:
the session, decision A1 (BYE), A3 (RESUME on request only), A5 (reconnect,
never ARM), E4 (the ESP must answer a new session's first frame), E2 (the
ESP's answers for the node's log), D2 (the FC values in STATE), the short TCP
write, and REQ-SAF-023 checked on the wire. No ROS.

Needs sd_protocol (pip install -e drone/ground/src/sd_protocol_py); skipped
without it.
"""

import ast
import pathlib
import re

import pytest

try:
    from fake_esp import TEST_PSK, TICK, Clock, FakeEsp

    from flight_interface import esp_backend
    from flight_interface.esp_backend import (
        HANDSHAKE_TIMEOUT,
        RECONNECT_AFTER,
        RECONNECT_EVERY,
        EspBackend,
        ack_reason,
    )
    from sd_protocol.frame import MsgType
    from sd_protocol.messages import (
        AGE_UNKNOWN,
        AckResult,
        ByeReason,
        EspState,
        EventCode,
        encode_u8_payload,
    )
except ModuleNotFoundError as error:  # no sd_protocol; any other import error fails
    if not (error.name or "").startswith("sd_protocol"):
        raise
    # A module-level skip would stop the collection of the whole folder
    # under the ROS plugins.
    esp_backend = None
    RECONNECT_AFTER = RECONNECT_EVERY = HANDSHAKE_TIMEOUT = TICK = 0.0

from flight_interface.backends import make_backend
from flight_interface.guard import NEUTRAL_WINDOW, ZERO, Guard

pytestmark = pytest.mark.skipif(esp_backend is None, reason="sd_protocol not installed")
PACKAGE = pathlib.Path(esp_backend.__file__).parent if esp_backend else None


@pytest.fixture
def fake():
    esp = FakeEsp()
    yield esp
    esp.close()


@pytest.fixture
def backend(fake):
    b = EspBackend(*fake.addr, TEST_PSK)
    yield b
    b.close()


@pytest.fixture
def clock(fake, backend):
    return Clock(fake, backend)


def test_make_backend_esp_and_bad_psk():
    b = make_backend("esp", esp_host="127.0.0.1", esp_port=9, psk=TEST_PSK)
    assert isinstance(b, EspBackend) and b.name == "esp"
    b.close()
    with pytest.raises(ValueError):
        EspBackend("127.0.0.1", 9, b"short")


# ---------- REQ-SAF-023: one path to ARM, the operator's ----------
def _scopes(source, name):
    """The scope (enclosing function or class, or "<module>") of every code
    reference to `name` in `source`: names, attributes and imports, at any
    level. Strings, docstrings and the `def` of a function called `name` do
    not count."""
    found = []

    def refs(node):
        if isinstance(node, ast.alias):
            return {node.name.rsplit(".", 1)[-1], node.asname}
        return {getattr(node, "attr", None) or getattr(node, "id", None)}

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, child.name)
                continue
            if name in refs(child):
                found.append(scope)
            visit(child, scope)

    visit(ast.parse(source), "<module>")
    return found


def _uses(name):
    """(file, scope) of every code reference to `name` in the flight_interface
    package."""
    found = []
    for path in sorted(PACKAGE.glob("*.py")):
        found += [(path.name, scope) for scope in _scopes(path.read_text(), name)]
    return sorted(set(found))


def test_the_static_check_also_sees_module_and_class_level_code():
    source = (
        "from sd_protocol.ground import arm\n"
        "session.arm(8)\n"
        "class C:\n"
        "    a = session.arm\n"
        "    def arm(self):\n"
        "        return self.arm\n"
        "'''session.arm(8) in a string'''\n"
    )
    assert sorted(_scopes(source, "arm")) == ["<module>", "<module>", "C", "arm"]


def test_arm_is_built_and_sent_only_on_the_operator_path():
    """Decisions A4, K6: the package never names ARM_CONTROL_REQUEST; the
    session's arm() is called only in EspBackend.arm_control();
    arm_control() only where the node sends the request the guard says is
    due; and the guard holds an ARM only from the node's ~/arm_control
    callback (its request_arm())."""
    assert _uses("ARM_CONTROL_REQUEST") == []
    assert _uses("arm") == [("esp_backend.py", "arm_control")]
    assert _uses("arm_control") == [("link_node.py", "_send_due_request")]
    assert _uses("request_arm") == [("link_node.py", "_on_arm_control")]


# ---------- session ----------
def test_opens_a_session_streams_neutral_and_reports_state(clock, fake, backend):
    state = clock.run(0.3)
    assert backend.session_open and fake.hellos == 1
    assert state["esp_state"] == "SESSION_IDLE"
    assert state["mask"] is None and state["airborne"] is False  # decision B1
    assert state["failsafe_output"] is False
    assert state["fc_link_ok"] is None  # unknown until the ESP says (#179)
    assert fake.count(MsgType.CONFIG_GET) == 1  # confirms the session
    assert clock.now - 0.1 < state["rx_time"] < clock.now  # a recent poll time
    assert fake.setpoints() > 10
    assert fake.arm_requests == 0


def test_every_setpoint_is_neutral_until_c1(clock, fake, backend):
    # A real command goes out as mode 0 too: nothing moves before decision C1.
    clock.run(0.1)
    for _ in range(5):
        backend.send_setpoint((0.3, 0.0, 0.0, 0.5), clock.now)
        clock.run(TICK, send=False)
    assert fake.setpoint_modes and set(fake.setpoint_modes) == {0}


def test_no_state_while_not_sending_is_not_a_lost_session(clock, fake, backend):
    clock.run(0.2)
    clock.run(RECONNECT_AFTER + 1.0, send=False)  # node inactive: nothing sent
    assert backend.session_open and fake.hellos == 1


def test_wrong_psk_never_opens_and_retries_slowly(fake):
    backend = EspBackend(*fake.addr, bytes(32))
    clock = Clock(fake, backend)
    clock.run(RECONNECT_EVERY * 2 + 0.5)
    assert not backend.session_open
    assert fake.hellos == 0  # the fake refuses HELLO tagged with another PSK
    assert "HELLO_ACK" in backend.last_error
    assert 2 <= backend.errors <= 3  # one attempt every RECONNECT_EVERY
    backend.close()


def test_no_gateway_is_an_error_not_an_exception(fake):
    addr = fake.addr
    fake.close()
    backend = EspBackend(*addr, TEST_PSK)
    for i in range(10):
        assert backend.poll(i * TICK) == []
        backend.send_setpoint(ZERO, i * TICK)
    assert backend.errors >= 1 and not backend.session_open
    assert backend.request_resume(0.2, backend.session_id) is False
    backend.close()
    assert backend.poll(1.0) == []


# ---------- A5: reconnect, never ARM ----------
def test_a_late_hello_ack_is_dropped_and_a_new_handshake_follows(
    clock, fake, backend, monkeypatch
):
    # REQ-SAF-024 (#204): the first HELLO_ACK comes 150 ms after the HELLO (a
    # fake clock), so no session opens; the next attempt, with a new
    # GroundSession and so a new nonce, answers in time and opens one.
    from sd_protocol.ground import GroundSession

    made = []

    def session(psk):
        step_us = 150_000 if not made else 1  # per clock read
        now_us = [0]

        def clock_us():
            now_us[0] += step_us
            return now_us[0]

        made.append(GroundSession(psk, clock_us=clock_us))
        return made[-1]

    monkeypatch.setattr(esp_backend, "GroundSession", session)
    clock.run(0.3)
    assert not backend.session_open and backend.late_hello_acks == 1
    assert "REQ-SAF-024" in backend.last_error
    clock.run(RECONNECT_EVERY + 0.5)
    assert backend.session_open and fake.hellos == 2
    assert made[0].nonce != made[1].nonce  # a nonce never repeats (spec §1)
    assert fake.arm_requests == 0


def test_reconnects_after_an_esp_restart_without_arm(clock, fake, backend):
    clock.run(0.3)
    first = backend.session_id
    fake.restart()
    clock.run(RECONNECT_AFTER + RECONNECT_EVERY + 1.0)
    assert backend.session_open and backend.session_id != first
    assert backend.sessions_opened == 2
    assert fake.state == EspState.SESSION_IDLE  # waits for the operator
    assert fake.arm_requests == 0


def test_reconnects_when_tcp_drops_outside_streaming(clock, fake, backend):
    clock.run(0.3)
    fake.close_tcp()
    clock.run(RECONNECT_EVERY + 0.5)
    assert backend.sessions_opened == 2 and backend.tcp_open
    assert fake.arm_requests == 0


def test_tcp_drop_while_streaming_keeps_the_session_on_udp(clock, fake, backend):
    clock.run(0.3)
    fake.force_state(EspState.STREAMING)
    clock.run(0.2)
    fake.close_tcp()
    state = clock.run(1.0)
    assert backend.sessions_opened == 1 and not backend.tcp_open  # ADR-005
    assert state["esp_state"] == "STREAMING"
    assert (
        backend.request_resume(clock.now, backend.session_id) is False
    )  # RESUME needs TCP


def test_the_esp_ending_the_session_gives_a_new_one(clock, fake, backend):
    clock.run(0.3)
    fake.send_bye(ByeReason.TIMEOUT)
    clock.run(RECONNECT_EVERY + 0.5)
    assert backend.sessions_opened == 2
    assert fake.arm_requests == 0


# ---------- A1: BYE ----------
def test_end_session_sends_bye_and_reconnects_later_without_arm(clock, fake, backend):
    clock.run(0.3)
    fake.force_state(EspState.STREAMING)
    clock.run(0.1)
    backend.end_session(clock.now)
    fake.step(clock.now)
    assert fake.byes_received == 1
    assert fake.state == EspState.NO_SESSION and fake.failsafe_output
    assert not backend.session_open
    state = clock.run(RECONNECT_EVERY + 0.5)
    assert backend.sessions_opened == 2
    # The new session starts while the ADR-004 output still runs: EVENT 0x0104.
    assert backend.failsafe_output and state["failsafe_output"] is True
    assert state["esp_state"] == "SESSION_IDLE"
    assert fake.arm_requests == 0


def test_close_sends_bye(clock, fake, backend):
    clock.run(0.3)
    backend.close()
    fake.step(clock.now)
    assert fake.byes_received == 1
    assert backend.poll(clock.now) == []


# ---------- link failsafe and A3: RESUME ----------
def test_link_failsafe_then_resume_on_request(clock, fake, backend):
    clock.run(0.3)
    fake.force_state(EspState.STREAMING)
    clock.run(0.2)
    clock.run(0.4, send=False)  # the ground goes quiet: T_loss
    state = clock.run(0.1)
    assert fake.state == EspState.LINK_FAILSAFE
    assert state["esp_state"] == "LINK_FAILSAFE" and state["failsafe_output"] is True
    clock.run(0.7)  # 0.7 s of neutral
    assert backend.request_resume(clock.now, backend.session_id) is True
    state = clock.run(0.2)
    assert backend.last_ack == ("RESUME_ACK", 0)
    assert fake.state == EspState.STREAMING
    assert state["esp_state"] == "STREAMING" and state["failsafe_output"] is False
    assert fake.arm_requests == 0


def test_resume_too_early_is_refused_by_the_esp(clock, fake, backend):
    clock.run(0.3)
    fake.force_state(EspState.STREAMING)
    clock.run(0.2)
    clock.run(0.4, send=False)
    clock.run(0.1)
    assert backend.request_resume(clock.now, backend.session_id) is True
    clock.run(0.1)
    assert backend.last_ack == ("RESUME_ACK", 3)  # RESUME_CONDITION_NOT_MET
    assert fake.state == EspState.LINK_FAILSAFE


def test_events_set_and_clear_failsafe_output(clock, fake, backend):
    clock.run(0.3)
    fake.send_event(EventCode.FAILSAFE_OUTPUT_ACTIVE)
    clock.run(0.1)
    assert backend.failsafe_output is True
    fake.send_event(EventCode.LINK_RESUMED)
    clock.run(0.1)
    assert backend.failsafe_output is False
    fake.send_event(EventCode.SETPOINT_STALE)
    clock.run(0.1)
    assert backend.failsafe_output is False


# ---------- bad input from the ESP side ----------
def test_bad_or_unknown_states_never_refresh(clock, fake, backend):
    clock.run(0.3)
    fake.send_state = False
    before = len(clock.states)
    for _ in range(5):
        fake.send_bad_state()
        fake.send_state_value(9)  # not an EspState
        clock.run(TICK)
    assert len(clock.states) == before


def test_tcp_framing_error_closes_tcp(clock, fake, backend):
    clock.run(0.3)
    fake.send_tcp_garbage()
    clock.run(0.1)
    assert "framing" in backend.last_error


def test_frames_sent_are_only_hello_setpoint_bye_resume(clock, fake, backend):
    """REQ-SAF-023 on the wire, over everything above in one run."""
    clock.run(0.3)
    fake.force_state(EspState.STREAMING)
    clock.run(0.2)
    clock.run(0.4, send=False)
    clock.run(0.8)
    backend.request_resume(clock.now, backend.session_id)
    clock.run(0.2)
    backend.end_session(clock.now)
    clock.run(RECONNECT_EVERY + 0.5)
    fake.restart()
    clock.run(RECONNECT_AFTER + RECONNECT_EVERY + 1.0)
    types = {t for _, t, _ in fake.received}
    assert types <= {
        MsgType.HELLO,
        MsgType.CONFIG_GET,
        MsgType.SETPOINT,
        MsgType.BYE,
        MsgType.RESUME_REQUEST,
    }
    assert fake.arm_requests == 0


# ---------- pending session (ESP Link State Machine §1, #156) ----------
def test_a_new_session_is_confirmed_even_when_nothing_is_sent(clock, fake, backend):
    """The ESP keeps a new session only as pending while the old one exists,
    and drops it after 2 s without a frame under its keys. The backend
    confirms it at once with CONFIG_GET, so an inactive node keeps it."""
    clock.run(0.3)
    first = backend.session_id
    fake.close_tcp()
    clock.run(RECONNECT_EVERY + 3.0, send=False)  # inactive node: no setpoints
    assert backend.sessions_opened == 2
    assert fake.switches == 1 and fake.pending_expired == 0
    assert fake.session_id == backend.session_id != first
    assert fake.arm_requests == 0


# ---------- #179: FC link health (events, FC_HEALTH; E6, E6.1, E6.2) ----------
def test_fc_link_events_set_fc_link_ok(clock, fake, backend):
    fake.send_health = False  # the events alone
    clock.run(0.3)
    assert backend.fc_link_ok is None  # nothing said yet: unknown
    fake.send_event(EventCode.FC_LINK_LOST)
    state = clock.run(0.1)
    assert backend.fc_link_ok is False and state["fc_link_ok"] is False
    fake.send_event(EventCode.FC_LINK_RESTORED)
    state = clock.run(0.1)
    assert state["fc_link_ok"] is True
    fake.restart()  # a new session starts unknown again
    clock.run(RECONNECT_AFTER + RECONNECT_EVERY + 1.0)
    assert backend.sessions_opened == 2 and clock.states[-1]["fc_link_ok"] is None


def test_fc_health_gives_the_fc_state_in_a_new_session(clock, fake, backend):
    """E6: a session that starts while the FC is steady gets no event, but
    FC_HEALTH (5 Hz) says it. E6.2: alive only after FC_RESTORE_AFTER of
    fc_alive 1, so about 0.4 s after the first setpoint."""
    clock.run(0.15)
    assert backend.fc_link_ok is None
    clock.run(0.25)
    assert backend.fc_link_ok is None  # one or two FC_HEALTH: not 250 ms yet
    clock.run(0.4)
    assert backend.fc_link_ok is True and clock.states[-1]["fc_link_ok"] is True


def test_fc_alive_0_is_lost_at_once_and_alive_again_only_after_250_ms(
    clock, fake, backend
):
    clock.run(0.8)
    fake.fc_health["fc_alive"] = 0
    clock.run(0.22)  # the next FC_HEALTH says it
    assert backend.fc_link_ok is False
    fake.fc_health["fc_alive"] = 1
    clock.run(0.22)
    assert backend.fc_link_ok is False  # one fc_alive 1 is not enough (E6.2)
    clock.run(0.4)
    assert backend.fc_link_ok is True


def test_a_lost_event_wins_over_an_older_alive_fc_health(clock, fake, backend):
    """FC_LINK_LOST (TCP) restarts the 250 ms: an FC_HEALTH that still says
    alive does not undo it at once."""
    clock.run(0.8)
    fake.send_event(EventCode.FC_LINK_LOST)
    clock.run(0.1)
    assert backend.fc_link_ok is False
    clock.run(0.1)
    assert backend.fc_link_ok is False


def test_no_fc_health_for_a_second_makes_the_fc_state_unknown(clock, fake, backend):
    clock.run(0.8)
    assert backend.fc_link_ok is True
    fake.send_health = False
    clock.run(0.5)
    assert backend.fc_link_ok is True
    clock.run(1.0)
    assert backend.fc_link_ok is None  # E6.1: unknown, so not OK


@pytest.mark.parametrize("raw, armed", [(1, True), (0, False), (0xFF, None)])
def test_armed_comes_from_fc_health(clock, fake, backend, raw, armed):
    """D1: armed true / false / null; airborne = armed is true."""
    fake.fc_health["armed"] = raw
    state = clock.run(0.5)
    assert state["armed"] is armed
    assert state["airborne"] is (armed is True)


def test_armed_is_unknown_without_fc_health(clock, fake, backend):
    fake.send_health = False
    state = clock.run(0.5)
    assert state["armed"] is None and state["airborne"] is False


def test_fc_ages_from_fc_health(clock, fake, backend):
    """D2.1: each value's age = its age in FC_HEALTH + the time since."""
    fake.fc_health["attitude_age_ms"] = 30
    fake.fc_health["analog_age_ms"] = AGE_UNKNOWN
    state = clock.run(0.5)
    assert 0.03 <= state["fc_ages"]["attitude"] <= 0.03 + 0.2 + TICK
    assert state["fc_ages"]["analog"] is None
    fake.send_health = False
    state = clock.run(1.2)
    assert state["fc_ages"] == {"attitude": None, "analog": None}


def test_a_malformed_fc_health_is_dropped_and_never_a_state(clock, fake, backend):
    """A wrong-size FC_HEALTH is rejected by the receive checks (counted),
    raises nothing and never counts as a STATE for the watchdog."""
    clock.run(0.3)
    fake.send_state = False
    fake.send_health = False
    before, rejected = len(clock.states), backend.rx_rejected
    for _ in range(5):
        fake.send_udp_frame(0x81, bytes(24))
        clock.run(TICK)
    assert len(clock.states) == before
    assert backend.rx_rejected == rejected + 5


def test_fc_health_alone_never_refreshes_the_watchdog(clock, fake, backend):
    clock.run(0.3)
    fake.send_state = False  # FC_HEALTH goes on
    before = len(clock.states)
    clock.run(1.0)
    assert len(clock.states) == before


def test_the_esps_rx_rejected_wraps_are_counted_per_session(clock, fake, backend):
    """#149: STATE.rx_rejected is a u16 that wraps; the total goes through
    WrapCounter and starts again in every session."""
    fake.rx_rejected = 65530
    assert clock.run(0.2)["esp_rx_rejected"] == 65530
    fake.rx_rejected = 5  # wrapped
    assert clock.run(0.2)["esp_rx_rejected"] == 65536 + 5
    fake.restart()
    fake.rx_rejected = 3  # a reboot, not a wrap
    clock.run(RECONNECT_AFTER + RECONNECT_EVERY + 1.0)
    assert backend.sessions_opened == 2
    assert clock.states[-1]["esp_rx_rejected"] == 3


def test_arm_refused_fc_not_answering_is_kept_raw(clock, fake, backend):
    """#179 D2: ARM_CONTROL_ACK result 4. This node never sends ARM; a stray
    ACK is recorded, not raised."""
    clock.run(0.3)
    fake.send_tcp_message(MsgType.ARM_CONTROL_ACK, encode_u8_payload(4))
    clock.run(0.1)
    assert backend.last_ack == ("ARM_CONTROL_ACK", 4)


@pytest.mark.parametrize("streamed, result", [(0.6, 4), (1.5, 0)])
def test_the_esp_wants_1_s_of_streaming_before_arm(
    clock, fake, backend, streamed, result
):
    """#188 as the fake ESP models it: after 0.6 s of setpoints (the old K6
    window) ARM is refused with result 4; after 1.5 s (K6 now) it is OK."""
    fake.rc_ack_needed = 1.0
    clock.run(streamed)
    assert backend.arm_control(8, clock.now, backend.session_id)
    clock.run(0.1)
    assert backend.last_ack == ("ARM_CONTROL_ACK", result)


# ---------- A4: ARM on the operator's request; B1: the mask from the ACK ----------
def test_arm_on_request_streams_and_reports_the_confirmed_mask(clock, fake, backend):
    state = clock.run(0.3)
    assert state["mask"] is None  # nothing confirmed yet
    assert backend.arm_control(8, clock.now, backend.session_id) is True
    state = clock.run(0.2)
    assert fake.arm_requests == 1 and fake.state == EspState.STREAMING
    assert backend.last_ack == ("ARM_CONTROL_ACK", 0)
    assert state["mask"] == 8 and state["esp_state"] == "STREAMING"
    assert state["override_active"] is True
    assert set(fake.setpoint_modes) == {0}  # still neutral until C1


def test_arm_refused_by_the_esp_gives_no_mask(clock, fake, backend):
    clock.run(0.3)
    fake.force_state(EspState.LINK_FAILSAFE)
    backend.arm_control(11, clock.now, backend.session_id)
    state = clock.run(0.2)
    assert backend.last_ack == ("ARM_CONTROL_ACK", 1)  # WRONG_STATE
    assert state["mask"] is None


def test_arm_refused_locally(clock, fake, backend):
    assert backend.arm_control(8, 0.0, backend.session_id) is False  # no session yet
    clock.run(0.3)
    assert backend.arm_control(9, clock.now, backend.session_id) is False  # not a mask
    assert fake.arm_requests == 0


def test_a_new_session_forgets_the_mask_and_never_re_arms(clock, fake, backend):
    clock.run(0.3)
    backend.arm_control(8, clock.now, backend.session_id)
    assert clock.run(0.2)["mask"] == 8
    fake.restart()
    state = clock.run(RECONNECT_AFTER + RECONNECT_EVERY + 1.0)
    assert backend.sessions_opened == 2
    assert state["mask"] is None and state["esp_state"] == "SESSION_IDLE"
    assert fake.arm_requests == 1  # only the operator's one


# ---------- helpers for the tests below ----------
def run_until(clock, condition, limit, send=True):
    """Steps the clock one tick at a time until condition() holds. Returns the
    time of the poll that made it hold (clock.now is one tick later), or None."""
    end = clock.now + limit
    while clock.now < end - 1e-9:
        polled_at = clock.now
        clock.run(TICK, send=send)
        if condition():
            return polled_at
    return None


# ---------- E4: the ESP must answer the first frame (ADR-005) ----------
@pytest.mark.parametrize("active", [False, True], ids=["inactive", "active"])
def test_a_replayed_hello_ack_is_dropped_after_the_handshake_timeout(
    clock, fake, backend, active
):
    """A recorded HELLO_ACK replayed into a new connection passes the PSK
    check, but the keys the ground derives from it are not the ESP's, so
    nothing comes back. Before E4 an inactive node kept that session for good
    and an active one for RECONNECT_AFTER (3 s)."""
    clock.run(0.3, send=active)
    first = backend.session_id
    assert backend.sessions_unconfirmed == 0
    fake.hello_fault = "replay_ack"
    fake.reset_tcp()  # the ground has to connect again
    opened = run_until(
        clock, lambda: backend.sessions_opened == 2, RECONNECT_EVERY + 1.0, active
    )
    assert opened is not None
    assert backend.session_id == first  # it really took the old HELLO_ACK
    dropped = run_until(clock, lambda: not backend.session_open, 3.0, active)
    assert dropped is not None
    assert HANDSHAKE_TIMEOUT < dropped - opened <= HANDSHAKE_TIMEOUT + 2 * TICK
    assert backend.sessions_unconfirmed == 1
    assert "no answer to the first frame" in backend.last_error

    fake.hello_fault = None  # the attacker is gone: the next handshake works
    assert run_until(
        clock, lambda: backend.sessions_opened == 3, RECONNECT_EVERY + 1.0, active
    )
    clock.run(3.0, send=active)
    assert backend.session_open and backend.session_id == fake.session_id != first
    assert backend.sessions_opened == 3 and backend.sessions_unconfirmed == 1
    assert fake.arm_requests == 0  # REQ-SAF-023: no handshake ever sends ARM


def test_no_config_ack_while_inactive_redoes_the_handshake_without_arm(
    clock, fake, backend
):
    """A gateway that took HELLO but lost the session (here: it never answers
    CONFIG_GET). Inactive, so no STATE either: the only proof is missing."""
    fake.answer_config = False
    clock.run(2 * RECONNECT_EVERY + 0.5, send=False)  # attempts at 0, 2 and 4 s
    assert backend.sessions_opened == 3 and backend.sessions_unconfirmed == 2
    fake.answer_config = True
    clock.run(RECONNECT_EVERY + 4.0, send=False)
    assert backend.session_open and backend.session_id == fake.session_id
    unconfirmed = backend.sessions_unconfirmed
    clock.run(3.0, send=False)
    assert backend.sessions_unconfirmed == unconfirmed  # an answered one stays
    assert fake.arm_requests == 0


def test_a_state_also_proves_the_session(clock, fake, backend):
    """Any frame under the session's keys counts, not only the CONFIG_ACK."""
    fake.answer_config = False
    clock.run(3.0)  # active: the setpoints bring STATE back
    assert backend.session_open
    assert backend.sessions_opened == 1 and backend.sessions_unconfirmed == 0


def test_an_answered_session_stays_while_inactive(clock, fake, backend):
    clock.run(6.0, send=False)
    assert backend.session_open and backend.sessions_opened == 1
    assert backend.sessions_unconfirmed == 0 and fake.hellos == 1


# ---------- a short TCP write ----------
class ShortSend:
    """The session's TCP socket with a nearly full send buffer: every send()
    takes only the first half of the bytes, as a non-blocking socket may."""

    def __init__(self, sock):
        self._sock = sock

    def send(self, data):
        return self._sock.send(data[: len(data) // 2])

    def __getattr__(self, name):
        return getattr(self._sock, name)


def test_a_short_tcp_write_closes_tcp_and_the_cut_frame_never_lands(
    clock, fake, backend
):
    clock.run(0.3)
    errors = backend.errors
    backend._tcp = ShortSend(backend._tcp)
    assert backend.arm_control(8, clock.now, backend.session_id) is False
    assert not backend.tcp_open
    assert backend.errors == errors + 1
    match = re.fullmatch(
        r"tcp send: short write, (\d+) of (\d+) bytes", backend.last_error
    )
    assert match and 0 < int(match[1]) < int(match[2])
    # The half frame is never completed later: the ESP gets no ARM, and the
    # backend opens a new session (SESSION_IDLE without TCP is over).
    clock.run(RECONNECT_EVERY + 1.0)
    assert fake.arm_requests == 0 and fake.state == EspState.SESSION_IDLE
    assert backend.session_open and backend.tcp_open
    assert backend.sessions_opened == 2 and backend.armed_mask is None


# ---------- E2: every ARM / RESUME answer, once, with its reason ----------
def test_take_acks_gives_every_answer_once_with_its_reason(clock, fake, backend):
    clock.run(0.3)
    assert backend.take_acks() == []
    assert backend.arm_control(
        11, clock.now, backend.session_id
    )  # the gateway runs mask 8 only
    clock.run(0.1)
    assert backend.take_acks() == [
        ("ARM_CONTROL_ACK", AckResult.MASK_MISMATCH, "MASK_MISMATCH")
    ]
    assert backend.take_acks() == []  # each answer once
    assert backend.request_resume(clock.now, backend.session_id)  # not in LINK_FAILSAFE
    clock.run(0.1)
    # Codes this ESP never sends to an idle request (4 comes with PR #183).
    fake.send_tcp_message(MsgType.ARM_CONTROL_ACK, encode_u8_payload(4))
    fake.send_tcp_message(MsgType.ARM_CONTROL_ACK, encode_u8_payload(9))
    clock.run(0.1)
    assert backend.arm_control(8, clock.now, backend.session_id)
    clock.run(0.1)
    assert backend.take_acks() == [
        ("RESUME_ACK", AckResult.WRONG_STATE, "WRONG_STATE"),
        ("ARM_CONTROL_ACK", 4, "FC_NOT_ANSWERING"),
        ("ARM_CONTROL_ACK", 9, "UNKNOWN_9"),
        ("ARM_CONTROL_ACK", AckResult.OK, "OK"),
    ]
    assert backend.armed_mask == 8  # the OK answer to our request


@pytest.mark.parametrize(
    "code, name",
    [
        (0, "OK"),
        (1, "WRONG_STATE"),
        (2, "MASK_MISMATCH"),
        (3, "RESUME_CONDITION_NOT_MET"),
        (4, "FC_NOT_ANSWERING"),
        (200, "UNKNOWN_200"),
    ],
)
def test_ack_reason_names(code, name):
    assert ack_reason(code) == name


# ---------- D2: the FC values of every STATE ----------
def test_every_state_carries_the_fc_values_as_the_gateway_sent_them(
    clock, fake, backend
):
    fake.fc_values = {"roll": -187, "pitch": 217, "heading": 359, "vbat": 1552}
    state = clock.run(0.3)
    assert state["fc_values"] == {
        "roll": -187,
        "pitch": 217,
        "heading": 359,
        "vbat": 1552,
    }
    fake.fc_values = dict(fake.fc_values, roll=5, vbat=0)
    state = clock.run(0.1)
    assert state["fc_values"]["roll"] == 5 and state["fc_values"]["vbat"] == 0


# ---------- K6: a held request stays in the session it was asked in ----------
def node_tick(guard, backend, fake, now):
    """The node's _tick() without ROS: poll, guard, send, held request."""
    fake.step(now)
    for state in backend.poll(now):
        guard.on_drone_state(state, state.get("rx_time", now))
    command = guard.command(now)
    if command is not None and backend.send_setpoint(command, now):
        guard.note_sent(command, now)
    due = guard.request_due(now)
    if due is not None:
        kind, mask, session = due
        if kind == "arm":
            backend.arm_control(mask, now, session)
        else:
            backend.request_resume(now, session)
    fake.step(now)


def run_node(guard, backend, fake, start, end):
    t = start
    while t < end - 1e-9:
        node_tick(guard, backend, fake, t)
        t = round(t + TICK, 6)
    return t


def test_a_held_arm_never_goes_out_on_a_session_opened_after_it(fake, backend):
    """Review of PR #187: TCP drops just before the neutral window is full,
    the backend reconnects at once (the last attempt was over RECONNECT_EVERY
    ago) while the guard's last STATE still shows the old session. The held
    ARM must not go out on the new session."""
    guard = Guard(tick_period=TICK)
    guard.activate()
    t = run_node(guard, backend, fake, 0.0, RECONNECT_EVERY + 0.5)
    first = backend.session_id
    ok, _ = guard.request_arm(8, t)
    assert ok
    t = run_node(guard, backend, fake, t, t + NEUTRAL_WINDOW - 0.1)
    fake.close_tcp()
    # Worst case: the new session's first STATE is late, so the guard's last
    # STATE (the old session) is still fresh when the window fills.
    fake.send_state = False
    run_node(guard, backend, fake, t, t + 0.6)
    assert backend.sessions_opened == 2
    fake.send_state = True
    run_node(guard, backend, fake, t + 0.6, t + 2.0)
    assert backend.sessions_opened == 2 and backend.session_id != first
    assert fake.arm_requests == 0
    assert not guard.arm_pending  # dropped, the operator asks again


def test_arm_and_resume_only_in_the_session_they_were_asked_in(clock, fake, backend):
    clock.run(0.3)
    old = backend.session_id
    fake.close_tcp()
    clock.run(RECONNECT_EVERY + 0.5)
    assert backend.session_open and backend.session_id != old
    assert backend.arm_control(8, clock.now, old) is False
    assert backend.request_resume(clock.now, old) is False
    clock.run(0.1)
    assert fake.arm_requests == 0 and fake.count(MsgType.RESUME_REQUEST) == 0


def test_no_arm_on_a_session_the_esp_has_not_confirmed(clock, fake, backend):
    """E4 as a second line for K6: before the CONFIG_ACK or a STATE proves
    the session, ARM and RESUME stay on the ground."""
    fake.answer_config = False
    clock.run(0.2, send=False)  # inactive: no STATE either
    assert backend.session_open
    assert backend.arm_control(8, clock.now, backend.session_id) is False
    assert backend.request_resume(clock.now, backend.session_id) is False
    assert fake.arm_requests == 0


def test_a_setpoint_counts_only_when_it_went_out(clock, fake, backend):
    assert backend.send_setpoint(ZERO, 0.0) is False  # no session yet
    clock.run(0.3)
    assert backend.send_setpoint(ZERO, clock.now) is True
    assert backend.send_neutral(clock.now) is True


def test_end_session_says_whether_bye_went_out(clock, fake, backend):
    clock.run(0.3)
    assert backend.end_session(clock.now) is True
    fake.step(clock.now)
    assert fake.byes_received == 1
    assert backend.end_session(clock.now) is False  # no session left


def test_bye_after_unread_bytes_still_reaches_the_esp(clock, fake, backend):
    """An EVENT the ground has not read yet must not turn the close after BYE
    into a reset that loses the BYE."""
    clock.run(0.3)
    fake.send_event(EventCode.FC_LINK_LOST)
    fake.step(clock.now)  # queued on the ground's socket, not read
    assert backend.end_session(clock.now) is True
    for _ in range(5):
        fake.step(clock.now)
    assert fake.byes_received == 1


def test_a_malformed_ack_is_ignored(clock, fake, backend, monkeypatch):
    """A wrong-size ACK is dropped by the receive checks (BAD_PAYLOAD_SIZE);
    one that would still decode to None is logged and ignored: no answer is
    recorded and the ARM stays requested."""
    clock.run(0.3)
    rejected = backend.rx_rejected
    fake._send_tcp(MsgType.ARM_CONTROL_ACK, b"")
    clock.run(0.04)
    assert backend.rx_rejected == rejected + 1 and backend.take_acks() == []
    monkeypatch.setattr(esp_backend, "decode_u8_payload", lambda payload: None)
    assert backend.arm_control(8, clock.now, backend.session_id)
    clock.run(0.1)  # the fake's real ACK arrives and "decodes" to None
    assert "malformed ARM_CONTROL_ACK" in backend.last_error
    assert backend.take_acks() == [] and backend.last_ack is None
    assert backend._arm_requested == 8


def test_the_confirmed_mask_goes_when_the_esp_leaves_streaming(clock, fake, backend):
    clock.run(0.3)
    backend.arm_control(8, clock.now, backend.session_id)
    assert clock.run(0.2)["mask"] == 8
    fake.force_state(EspState.SESSION_IDLE)  # e.g. a disarm ended control
    state = clock.run(0.2)
    assert state["esp_state"] == "SESSION_IDLE" and state["mask"] is None
    assert backend.armed_mask is None


def test_the_fake_rate_limits_hello_like_the_gateway(fake):
    """ADR-005 entry 2026-11-06: a second HELLO within 500 ms is not answered."""
    first = EspBackend(*fake.addr, TEST_PSK)
    second = EspBackend(*fake.addr, TEST_PSK)
    try:
        for b in (first, second):
            for _ in range(5):
                fake.step(0.0)
                b.poll(0.0)
                fake.step(0.0)
        assert first.session_open and fake.hellos == 1
        assert fake.hellos_rate_limited == 1 and not second.session_open
    finally:
        first.close()
        second.close()
