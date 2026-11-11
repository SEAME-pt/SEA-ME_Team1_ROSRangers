"""Faults and hostile input for esp_backend.py, against the fake ESP on
localhost. No ROS. The contract under test: poll() and the send calls never
raise into the node (an exception in the node's tick stops the node), a frame
that fails the receive checks changes nothing, and nothing here ever sends
ARM (REQ-SAF-023).

  * the address is checked once, at configure (no DNS in the 50 Hz tick);
  * a flood on the UDP port cannot hold up the node's tick, so ~/estop is
    still served (single-threaded executor);
  * forged and replayed frames (#109 "garbage: malformed, bad HMAC, replayed")
    never refresh the watchdog, set the mask, change fc_link_ok or end the
    session;
  * a broken handshake, a TCP reset, ICMP refused on UDP: counted as errors,
    then a new session (decision A5), never ARM;
  * an inactive node (no setpoints, so no STATE) still notices that its
    session is gone after a TCP drop;
  * a refused ARM does not clear a mask the ESP confirmed earlier (B1).

Needs sd_protocol (pip install -e drone/ground/src/sd_protocol_py); skipped
without it.
"""

import errno
import socket

import pytest

try:
    from fake_esp import TEST_PSK, TICK, Clock, FakeEsp

    from flight_interface import esp_backend
    from flight_interface.esp_backend import (
        FC_LINK_LOST,
        FC_LINK_RESTORED,
        HANDSHAKE_TIMEOUT,
        RECONNECT_AFTER,
        RECONNECT_EVERY,
        EspBackend,
    )
    from sd_protocol.frame import MsgType
    from sd_protocol.messages import (
        AckResult,
        ByeReason,
        EspState,
        Event,
        EventCode,
        encode_event,
        encode_u8_payload,
    )
except ImportError:  # no sd_protocol: see test_esp_backend.py
    esp_backend = None
    RECONNECT_AFTER = RECONNECT_EVERY = HANDSHAKE_TIMEOUT = TICK = 0.0

pytestmark = pytest.mark.skipif(esp_backend is None, reason="sd_protocol not installed")


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


def streaming(clock, fake, backend):
    """An operator ARM with mask 8: the ESP is STREAMING, the mask confirmed."""
    clock.run(0.3)
    assert backend.arm_control(8, clock.now)
    state = clock.run(0.2)
    assert fake.state == EspState.STREAMING and state["mask"] == 8
    return state


# ---------- the address: checked once, at configure ----------
@pytest.mark.parametrize(
    "host, port",
    [
        ("127.0.0.1", 0),
        ("127.0.0.1", 70000),
        ("127.0.0.1", -1),
        ("127.0.0.1", "x"),
        ("", 5759),
        ("   ", 5759),
    ],
)
def test_a_bad_address_is_refused_when_the_backend_is_made(host, port):
    # A ValueError makes configure fail cleanly. Before, port 70000 raised
    # OverflowError from connect_ex() inside poll(), i.e. in the node's tick.
    with pytest.raises(ValueError):
        EspBackend(host, port, TEST_PSK)


def test_a_host_name_is_resolved_once_when_the_backend_is_made(monkeypatch):
    calls = []
    real = socket.getaddrinfo

    def counting(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", counting)
    b = EspBackend("localhost", 9, TEST_PSK)
    assert b.addr == ("127.0.0.1", 9) and calls == ["localhost"]
    for i in range(5):  # connect attempts use the address, never DNS again
        b.poll(i * RECONNECT_EVERY)
    assert calls == ["localhost"]
    b.close()


def test_an_unresolvable_host_is_a_value_error(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(ValueError, match="esp_host"):
        EspBackend("gateway.invalid", 5759, TEST_PSK)


def test_no_socket_left_is_an_error_not_an_exception(monkeypatch):
    b = EspBackend("127.0.0.1", 9, TEST_PSK)

    def no_socket(*args, **kwargs):
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(esp_backend.socket, "socket", no_socket)
    assert b.poll(0.0) == []
    assert b.errors == 1 and "Too many open files" in b.last_error
    assert b.poll(0.1) == []  # the next attempt waits RECONNECT_EVERY
    assert b.errors == 1
    monkeypatch.undo()
    b.close()


def test_no_udp_socket_at_hello_ack_drops_the_session_and_retries(
    clock, fake, backend, monkeypatch
):
    real = socket.socket

    class NoUdp(real):
        def __init__(self, family=-1, type=-1, *args, **kwargs):
            if type == socket.SOCK_DGRAM:
                raise OSError(errno.ENOBUFS, "No buffer space available")
            super().__init__(family, type, *args, **kwargs)

    monkeypatch.setattr(esp_backend.socket, "socket", NoUdp)
    clock.run(0.2)
    assert not backend.session_open and "udp" in backend.last_error
    assert backend.sessions_opened == 0
    monkeypatch.setattr(esp_backend.socket, "socket", real)
    clock.run(RECONNECT_EVERY)
    assert backend.session_open and fake.arm_requests == 0


def test_a_connect_refused_at_once_is_an_error(monkeypatch):
    class Unreachable(socket.socket):
        def connect_ex(self, addr):
            return errno.ENETUNREACH

    monkeypatch.setattr(esp_backend.socket, "socket", Unreachable)
    b = EspBackend("127.0.0.1", 9, TEST_PSK)
    assert b.poll(0.0) == [] and b.phase == esp_backend.IDLE
    assert b.errors == 1 and "unreachable" in b.last_error.lower()
    monkeypatch.undo()
    b.close()


def test_a_connect_that_never_completes_times_out_and_is_retried(fake, monkeypatch):
    class NeverWritable:
        def __init__(self):
            pass

        def register(self, *args):
            pass

        def poll(self, timeout):
            return []

    monkeypatch.setattr(esp_backend.select, "poll", NeverWritable)
    b = EspBackend(*fake.addr, TEST_PSK)  # a listener: the connect is in progress
    b.poll(0.0)
    assert b.phase == esp_backend.CONNECTING
    dropped = HANDSHAKE_TIMEOUT + 0.01
    b.poll(dropped)
    assert b.phase == esp_backend.IDLE and "HELLO_ACK" in b.last_error
    b.poll(dropped + RECONNECT_EVERY - 0.01)
    assert b.phase == esp_backend.IDLE  # the next try RECONNECT_EVERY after the drop
    b.poll(dropped + RECONNECT_EVERY)
    assert b.phase == esp_backend.CONNECTING
    monkeypatch.undo()
    b.close()


# ---------- a flood cannot hold up the node's tick ----------
def test_a_udp_flood_is_read_in_bounded_slices(clock, fake, backend, monkeypatch):
    """Junk from the ESP's address (a spoofed source on the Wi-Fi): one poll
    reads at most MAX_UDP_READS datagrams, so the tick ends and the node's
    single-threaded executor still serves ~/estop. The rest is read by the
    next polls, and the link comes back once real STATE arrives. (A small
    limit here: a default socket buffer holds fewer than the real 256.)"""
    assert esp_backend.MAX_UDP_READS >= 64  # a real burst is read in one tick
    monkeypatch.setattr(esp_backend, "MAX_UDP_READS", 16)
    clock.run(0.3)
    fake.send_state = False
    for _ in range(150):
        fake.send_udp_raw(b"SD" + bytes(46))
    before = backend.rx_rejected
    assert backend.poll(clock.now) == []
    assert backend.rx_rejected - before == 16
    fake.send_state = True
    clock.run(0.5)
    assert backend.rx_rejected - before == 150
    assert clock.states[-1]["rx_time"] > clock.now - 0.1  # STATE again
    assert fake.arm_requests == 0


def test_tcp_is_read_in_bounded_slices(clock, fake, backend, monkeypatch):
    """One 4 KiB read per poll here: frames cut at the slice edges are put
    back together, nothing is rejected, and the last event still lands."""
    monkeypatch.setattr(esp_backend, "MAX_TCP_READS", 1)
    clock.run(0.3)
    for _ in range(299):  # ~12 KB: several slices
        fake.send_event(EventCode.SETPOINT_STALE)
    fake.send_event(FC_LINK_LOST)
    backend.poll(clock.now)
    assert backend.fc_link_ok is None  # the last event is not read yet
    clock.run(0.2)
    assert backend.fc_link_ok is False
    assert backend.rx_rejected == 0 and backend.errors == 0 and backend.session_open


# ---------- forged and replayed frames (#109 garbage) ----------
def test_forged_tcp_frames_change_nothing(clock, fake, backend):
    """Tagged with a key that is not the session's: no ARM ACK OK can set a
    mask, no FC_LINK_RESTORED can open the gate, no BYE can end the session."""
    clock.run(0.3)
    fake.send_event(FC_LINK_LOST)
    clock.run(0.1)
    fake.force_state(EspState.LINK_FAILSAFE)  # the real ESP will refuse ARM
    first = backend.session_id
    assert backend.arm_control(8, clock.now)
    fake.forge_tcp(MsgType.ARM_CONTROL_ACK, encode_u8_payload(AckResult.OK))
    fake.forge_tcp(MsgType.EVENT, encode_event(Event(code=FC_LINK_RESTORED)))
    fake.forge_tcp(MsgType.EVENT, encode_event(Event(code=EventCode.LINK_RESUMED)))
    fake.forge_tcp(MsgType.BYE, encode_u8_payload(ByeReason.NORMAL))
    backend.poll(clock.now)  # the forged frames first, before the ESP answers
    assert backend.armed_mask is None and backend.fc_link_ok is False
    assert backend.session_open and backend.session_id == first
    assert backend.rx_rejected == 4
    state = clock.run(0.2)
    assert backend.last_ack == ("ARM_CONTROL_ACK", AckResult.WRONG_STATE)
    assert state["mask"] is None and state["fc_link_ok"] is False
    assert state["failsafe_output"] is True and backend.sessions_opened == 1


def test_a_replayed_state_never_refreshes_the_watchdog(clock, fake, backend):
    clock.run(0.3)
    fake.send_state = False
    n = len(clock.states)
    for _ in range(10):
        fake.replay_udp()
        clock.run(TICK)
    assert len(clock.states) == n  # the sequence window drops every copy
    assert backend.rx_rejected == 10


def test_an_ack_from_an_old_session_is_rejected_in_a_new_one(clock, fake, backend):
    streaming(clock, fake, backend)
    ack_ok = fake.last_tcp_frame  # genuine ARM_CONTROL_ACK OK of session 1
    assert ack_ok[3] == MsgType.ARM_CONTROL_ACK
    fake.restart()
    clock.run(RECONNECT_AFTER + RECONNECT_EVERY + 1.0)
    assert backend.sessions_opened == 2 and clock.states[-1]["mask"] is None
    assert backend.arm_control(8, clock.now)  # ARM pending in session 2
    fake.replay_tcp(ack_ok)
    backend.poll(clock.now)  # the replay arrives before the ESP's real ACK
    assert backend.armed_mask is None and backend.rx_rejected == 1


def test_a_malformed_state_payload_never_refreshes(clock, fake, backend):
    clock.run(0.3)
    fake.send_state = False
    n = len(clock.states)
    for _ in range(5):
        fake.send_udp_frame(MsgType.STATE, bytes(3))  # genuine tag, short payload
        clock.run(TICK)
    assert len(clock.states) == n


# ---------- broken handshakes ----------
def test_the_esp_hanging_up_on_hello_is_retried(clock, fake, backend):
    fake.hello_fault = "hang_up"
    clock.run(0.2)
    assert not backend.session_open and backend.phase == esp_backend.IDLE
    fake.hello_fault = None
    clock.run(RECONNECT_EVERY)
    assert backend.session_open and fake.arm_requests == 0


def test_garbage_instead_of_hello_ack_is_retried(clock, fake, backend):
    fake.hello_fault = "garbage"
    clock.run(0.2)
    assert not backend.session_open and "handshake" in backend.last_error
    fake.hello_fault = None
    clock.run(RECONNECT_EVERY)
    assert backend.session_open and fake.arm_requests == 0


def test_a_bye_right_after_hello_ack_ends_the_new_session(clock, fake, backend):
    """HELLO_ACK, BYE and one more frame in the same read: the frames after
    the BYE belong to no session and must not be handled (before: an
    AttributeError out of poll(), which stops the node)."""
    fake.after_hello = [
        (MsgType.BYE, encode_u8_payload(ByeReason.NORMAL)),
        (MsgType.EVENT, encode_event(Event(code=FC_LINK_LOST))),
    ]
    clock.run(TICK)  # connect
    fake.step(clock.now)  # the fake answers HELLO with all three frames
    assert backend.poll(clock.now) == []
    assert not backend.session_open and backend.fc_link_ok is None
    assert backend.sessions_opened == 1
    fake.after_hello = []
    clock.run(RECONNECT_EVERY + 0.5)
    assert backend.sessions_opened == 2 and fake.arm_requests == 0


def test_frames_after_a_bye_in_the_same_read_are_not_handled(clock, fake, backend):
    clock.run(0.3)
    fake.send_tcp_message(MsgType.BYE, encode_u8_payload(ByeReason.TIMEOUT))
    fake.send_event(FC_LINK_LOST)
    backend.poll(clock.now)
    assert not backend.session_open and backend.fc_link_ok is None


# ---------- TCP reset and ICMP refused ----------
def test_a_tcp_reset_outside_streaming_gives_a_new_session(clock, fake, backend):
    clock.run(0.3)
    fake.reset_tcp()
    clock.run(TICK)
    assert "tcp recv" in backend.last_error and not backend.tcp_open
    clock.run(RECONNECT_EVERY + 0.5)
    assert backend.sessions_opened == 2 and fake.arm_requests == 0


def test_a_tcp_reset_while_streaming_keeps_the_session_on_udp(clock, fake, backend):
    streaming(clock, fake, backend)
    fake.reset_tcp()
    state = clock.run(1.0)
    assert backend.sessions_opened == 1 and not backend.tcp_open  # ADR-005
    assert state["esp_state"] == "STREAMING" and state["mask"] == 8


def test_arm_on_a_reset_connection_is_refused_not_raised(clock, fake, backend):
    clock.run(0.3)
    fake.reset_tcp()
    sent = backend.arm_control(8, clock.now)
    if sent:  # the RST may not be seen by the first send: the second fails
        sent = backend.arm_control(8, clock.now)
    assert sent is False and not backend.tcp_open
    assert "tcp send" in backend.last_error
    assert backend.request_resume(clock.now) is False and backend.errors >= 1
    clock.run(0.1)
    assert fake.arm_requests == 0


def test_icmp_refused_on_udp_is_counted_then_a_new_session(clock, fake, backend):
    clock.run(0.3)
    fake.close_udp()  # the ESP's UDP port is gone, TCP stays
    for _ in range(3):  # the ICMP error of one send is raised by the next
        backend.send_neutral(clock.now)
    assert "udp send" in backend.last_error
    clock.run(0.5)
    assert backend.errors > 0 and "udp" in backend.last_error
    assert backend.session_open  # not yet RECONNECT_AFTER without STATE
    clock.run(RECONNECT_AFTER)  # no STATE while sending: the session is gone
    assert backend.sessions_opened == 2 and fake.switches == 1
    assert fake.arm_requests == 0


# ---------- an inactive node notices that its session is gone ----------
def test_an_inactive_node_reconnects_after_the_esp_restarts(clock, fake, backend):
    """No setpoints, so the ESP sends no STATE and esp_state stays unknown.
    The ESP ends a session outside STREAMING once its TCP is closed and no UDP
    comes (ESP Link State Machine §1), so the TCP drop alone must start a new
    session. Before: the dead session was kept until the node was activated
    and RECONNECT_AFTER had passed."""
    clock.run(0.3, send=False)
    assert backend.session_open and backend.esp_state is None
    fake.restart()
    clock.run(RECONNECT_EVERY + 0.5, send=False)
    assert backend.sessions_opened == 2 and backend.tcp_open
    assert fake.session_id == backend.session_id
    assert fake.arm_requests == 0


# ---------- B1: a refused ARM changes nothing ----------
def test_a_refused_second_arm_keeps_the_confirmed_mask(clock, fake, backend):
    """Two ~/arm_control calls within one STATE period (40 ms): the ESP
    accepts the first (STREAMING, mask 8) and refuses the second (wrong
    state). A refusal changes nothing on the ESP, so the mask stays. Before:
    the mask was cleared, the gate stayed closed while the ESP streamed, and
    the operator could not ARM again (not SESSION_IDLE)."""
    clock.run(0.3)
    assert backend.arm_control(8, clock.now) and backend.arm_control(8, clock.now)
    state = clock.run(0.2)
    assert fake.arm_requests == 2 and fake.state == EspState.STREAMING
    assert backend.last_ack == ("ARM_CONTROL_ACK", AckResult.WRONG_STATE)
    assert state["mask"] == 8 and backend.armed_mask == 8


def test_a_mask_the_gateway_does_not_run_is_refused(clock, fake, backend):
    clock.run(0.3)
    assert backend.arm_control(11, clock.now)
    state = clock.run(0.2)
    assert backend.last_ack == ("ARM_CONTROL_ACK", AckResult.MASK_MISMATCH)
    assert fake.state == EspState.SESSION_IDLE and state["mask"] is None
