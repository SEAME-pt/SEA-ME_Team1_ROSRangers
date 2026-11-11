"""Tests of esp_backend.py against the fake ESP (test/fake_esp.py) on localhost:
the session, decision A1 (BYE), A3 (RESUME on request only), A5 (reconnect,
never ARM) and REQ-SAF-023 checked on the wire. No ROS.

Needs sd_protocol (pip install -e drone/ground/src/sd_protocol_py); skipped
without it.
"""

import ast
import pathlib

import pytest

try:
    from fake_esp import TEST_PSK, TICK, Clock, FakeEsp

    from flight_interface import esp_backend
    from flight_interface.esp_backend import (
        FC_LINK_LOST,
        FC_LINK_RESTORED,
        RECONNECT_AFTER,
        RECONNECT_EVERY,
        EspBackend,
    )
    from sd_protocol.frame import MsgType
    from sd_protocol.messages import ByeReason, EspState, EventCode, encode_u8_payload
except ImportError:  # no sd_protocol. A module-level skip would stop the
    esp_backend = None  # collection of the whole folder under the ROS plugins.
    RECONNECT_AFTER = RECONNECT_EVERY = TICK = 0.0

from flight_interface.backends import make_backend
from flight_interface.guard import ZERO

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
    """Decision A4: the package never names ARM_CONTROL_REQUEST; the session's
    arm() is called only in EspBackend.arm_control(), and arm_control() only
    in the node's ~/arm_control callback."""
    assert _uses("ARM_CONTROL_REQUEST") == []
    assert _uses("arm") == [("esp_backend.py", "arm_control")]
    assert _uses("arm_control") == [("link_node.py", "_on_arm_control")]


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
    assert backend.request_resume(0.2) is False
    backend.close()
    assert backend.poll(1.0) == []


# ---------- A5: reconnect, never ARM ----------
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
    assert backend.request_resume(clock.now) is False  # RESUME needs TCP


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
    assert backend.request_resume(clock.now) is True
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
    assert backend.request_resume(clock.now) is True
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
    backend.request_resume(clock.now)
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


# ---------- #179: FC link health ----------
def test_fc_link_events_set_fc_link_ok(clock, fake, backend):
    clock.run(0.3)
    fake.send_event(FC_LINK_LOST)
    state = clock.run(0.1)
    assert backend.fc_link_ok is False and state["fc_link_ok"] is False
    fake.send_event(FC_LINK_RESTORED)
    state = clock.run(0.1)
    assert state["fc_link_ok"] is True
    fake.restart()  # a new session starts unknown again
    clock.run(RECONNECT_AFTER + RECONNECT_EVERY + 1.0)
    assert backend.sessions_opened == 2 and clock.states[-1]["fc_link_ok"] is None


def test_fc_health_without_a_layout_is_ignored(clock, fake, backend):
    """FC_HEALTH (0x81, #179 D3) is not decoded yet: dropped, no error, and it
    never counts as a STATE for the watchdog."""
    clock.run(0.3)
    fake.send_state = False
    before, errors = len(clock.states), backend.errors
    for _ in range(5):
        fake.send_udp_frame(0x81, bytes(24))
        clock.run(TICK)
    assert len(clock.states) == before and backend.errors == errors


def test_arm_refused_fc_not_answering_is_kept_raw(clock, fake, backend):
    """#179 D2: ARM_CONTROL_ACK result 4. This node never sends ARM; a stray
    ACK is recorded, not raised."""
    clock.run(0.3)
    fake.send_tcp_message(MsgType.ARM_CONTROL_ACK, encode_u8_payload(4))
    clock.run(0.1)
    assert backend.last_ack == ("ARM_CONTROL_ACK", 4)


# ---------- A4: ARM on the operator's request; B1: the mask from the ACK ----------
def test_arm_on_request_streams_and_reports_the_confirmed_mask(clock, fake, backend):
    state = clock.run(0.3)
    assert state["mask"] is None  # nothing confirmed yet
    assert backend.arm_control(8, clock.now) is True
    state = clock.run(0.2)
    assert fake.arm_requests == 1 and fake.state == EspState.STREAMING
    assert backend.last_ack == ("ARM_CONTROL_ACK", 0)
    assert state["mask"] == 8 and state["esp_state"] == "STREAMING"
    assert state["override_active"] is True
    assert set(fake.setpoint_modes) == {0}  # still neutral until C1


def test_arm_refused_by_the_esp_gives_no_mask(clock, fake, backend):
    clock.run(0.3)
    fake.force_state(EspState.LINK_FAILSAFE)
    backend.arm_control(11, clock.now)
    state = clock.run(0.2)
    assert backend.last_ack == ("ARM_CONTROL_ACK", 1)  # WRONG_STATE
    assert state["mask"] is None


def test_arm_refused_locally(clock, fake, backend):
    assert backend.arm_control(8, 0.0) is False  # no session yet
    clock.run(0.3)
    assert backend.arm_control(9, clock.now) is False  # not a mask
    assert fake.arm_requests == 0


def test_a_new_session_forgets_the_mask_and_never_re_arms(clock, fake, backend):
    clock.run(0.3)
    backend.arm_control(8, clock.now)
    assert clock.run(0.2)["mask"] == 8
    fake.restart()
    state = clock.run(RECONNECT_AFTER + RECONNECT_EVERY + 1.0)
    assert backend.sessions_opened == 2
    assert state["mask"] is None and state["esp_state"] == "SESSION_IDLE"
    assert fake.arm_requests == 1  # only the operator's one
