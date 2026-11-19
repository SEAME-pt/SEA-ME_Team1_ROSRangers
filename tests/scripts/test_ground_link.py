"""Unit tests of the bench ground (scripts/ground_link.py) against the Python
twin of the ESP-side checks (sd_protocol): no network, no ESP."""

import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import ground_link  # noqa: E402

from sd_protocol.crypto import derive_keys  # noqa: E402
from sd_protocol.frame import Header, MsgType, decode_frame, encode_frame  # noqa: E402
from sd_protocol.messages import (  # noqa: E402
    Event,
    EventCode,
    HelloAck,
    Role,
    Transport,
    decode_u8_payload,
    encode_event,
    encode_hello_ack,
)
from sd_protocol.receiver import (  # noqa: E402
    HandshakeResult,
    Receiver,
    RxResult,
    is_accepted,
    receive_handshake,
)

PSK = bytes(range(32))
ESP_NONCE = bytes([0x42] * 16)
SESSION = 0x01020304


def esp_ack() -> bytes:
    ack = HelloAck(session_id=SESSION, esp_nonce=ESP_NONCE, fw_version=b"sd-gw 0.2.0 ")
    return encode_frame(
        Header(type=MsgType.HELLO_ACK, session_id=SESSION, seq=1),
        encode_hello_ack(ack),
        PSK,
    )


def open_link() -> ground_link.GroundLink:
    link = ground_link.GroundLink(PSK, nonce=bytes([0x11] * 16))
    link.hello()  # REQ-SAF-024: a HELLO_ACK is only taken after our HELLO
    assert link.take_ack(esp_ack())
    return link


def test_hello_passes_the_esp_handshake_check():
    link = ground_link.GroundLink(PSK, nonce=bytes([0x11] * 16))
    result, frame = receive_handshake(Role.ESP, link.hello(), PSK)
    assert result == HandshakeResult.ACCEPTED
    assert frame.header.seq == 1 and frame.header.session_id == 0


def test_ack_with_the_wrong_psk_is_refused():
    link = ground_link.GroundLink(bytes(32), nonce=bytes([0x11] * 16))
    assert not link.take_ack(esp_ack())
    assert link.keys is None


def test_session_frames_are_accepted_by_the_esp_receivers():
    link = open_link()
    keys = derive_keys(PSK, bytes([0x11] * 16), ESP_NONCE)
    assert link.keys == keys
    udp = Receiver(Role.ESP, Transport.UDP)
    tcp = Receiver(Role.ESP, Transport.TCP)
    udp.start_session(SESSION, keys.ground_to_esp)
    tcp.start_session(SESSION, keys.ground_to_esp)
    for i in range(1, 4):
        result, frame = udp.receive(link.setpoint(1, roll=100 * i))
        assert result == RxResult.ACCEPTED and frame.header.seq == i
    result, frame = tcp.receive(link.arm(8))
    assert result == RxResult.ACCEPTED
    assert frame.header.seq == 2  # HELLO used 1
    assert decode_u8_payload(frame.payload) == 8


def test_every_garbage_frame_is_rejected_by_the_esp():
    link = open_link()
    udp = Receiver(Role.ESP, Transport.UDP)
    udp.start_session(SESSION, link.keys.ground_to_esp)
    for _ in range(3):
        assert is_accepted(udp.receive(link.setpoint(0))[0])
    garbage = link.garbage()
    assert [name for name, _ in garbage] == [
        "bad tag",
        "replay (duplicate seq)",
        "old seq",
        "bad mode",
        "random bytes",
    ]
    for name, frame in garbage:
        result, _ = udp.receive(frame)
        assert not is_accepted(result), name
    # The stream goes on afterwards: garbage never blocks the real ground.
    assert is_accepted(udp.receive(link.setpoint(0))[0])


def test_arm_is_never_sent_without_the_operator():
    # REQ-SAF-023: only GroundLink.arm() builds ARM; nothing else does.
    link = open_link()
    keys = link.keys
    frames = [link.setpoint(0) for _ in range(5)] + [f for _, f in link.garbage()]
    frames.append(link.tcp(MsgType.RESUME_REQUEST))
    for frame in frames:
        err, f = decode_frame(frame, keys.ground_to_esp)
        if f is not None:
            assert f.header.type != MsgType.ARM_CONTROL_REQUEST


def test_esp_frames_are_described():
    link = open_link()
    esp_key = link.keys.esp_to_ground
    event = encode_frame(
        Header(type=MsgType.EVENT, session_id=SESSION, seq=2),
        encode_event(Event(code=EventCode.LINK_FAILSAFE_ENTERED, text=b"")),
        esp_key,
    )
    assert link.describe_tcp(event) == "EVENT LINK_FAILSAFE_ENTERED"
    assert "rejected" in link.describe_tcp(event)  # replayed: duplicate seq


def test_tcp_reader_cuts_frames():
    link = open_link()
    a, b = link.arm(8), link.tcp(MsgType.RESUME_REQUEST)
    reader = ground_link.TcpReader()
    data = a + b
    assert reader.feed(data[:10]) == []
    assert reader.feed(data[10:]) == [a, b]


def test_frames_after_the_ack_in_the_same_read_are_kept():
    # The ESP sends EVENT 0x0104 right after HELLO_ACK; both can arrive in one
    # TCP read, and the EVENT must not be lost.
    link = ground_link.GroundLink(PSK, nonce=bytes([0x11] * 16))
    link.hello()
    keys = derive_keys(PSK, bytes([0x11] * 16), ESP_NONCE)
    event = encode_frame(
        Header(type=MsgType.EVENT, session_id=SESSION, seq=2),
        encode_event(Event(code=EventCode.FAILSAFE_OUTPUT_ACTIVE, text=b"")),
        keys.esp_to_ground,
    )
    rest = ground_link.take_ack(link, ground_link.TcpReader().feed(esp_ack() + event))
    assert rest == [event]
    assert link.describe_tcp(rest[0]) == "EVENT FAILSAFE_OUTPUT_ACTIVE"
    assert ground_link.take_ack(ground_link.GroundLink(PSK), [event]) is None


def test_gen_psk_creates_a_private_file_and_never_replaces(tmp_path):
    path = tmp_path / "seame-drone" / "secrets.json"
    path.parent.mkdir()
    path.write_text('{"ota": "keep-me-ota"}')
    psk = ground_link.gen_psk(path)
    assert len(psk) == 64
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    data = __import__("json").loads(path.read_text())
    assert data == {"ota": "keep-me-ota", "psk": psk}  # other secrets kept
    try:
        ground_link.gen_psk(path)
        raise AssertionError("replaced an existing PSK")
    except SystemExit:
        pass
    assert ground_link.load_psk(path) == bytes.fromhex(psk)


def test_bad_psks_are_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("SEAME_PSK", raising=False)
    for text in ("ab" * 31, "ab" * 33, "zz" * 32, " ab" * 21 + "a"):
        try:
            ground_link.parse_psk(text)
            raise AssertionError(f"accepted {text!r}")
        except SystemExit:
            pass
    assert ground_link.parse_psk(" " + "AB" * 32 + "\n") == bytes([0xAB] * 32)
    monkeypatch.setenv("SEAME_PSK", "cd" * 32)
    assert ground_link.load_psk(tmp_path / "none.json") == bytes([0xCD] * 32)


def test_when_a_new_session_is_needed():
    from sd_protocol.messages import EspState

    new = ground_link.needs_new_session
    assert new(3.5, True, EspState.STREAMING)  # no STATE: session gone
    assert not new(0.2, True, EspState.LINK_FAILSAFE)  # TCP alive: RESUME works
    # Bench 2026-11-09: back from a Wi-Fi drop, STATE flows, TCP was closed by
    # the drone's keep-alive -> only a new session can leave LINK_FAILSAFE.
    assert new(0.2, False, EspState.LINK_FAILSAFE)
    assert new(0.2, False, EspState.SESSION_IDLE)  # cannot ARM without TCP
    assert not new(0.2, False, EspState.STREAMING)  # ADR-005: keep flying on UDP
    assert not new(0.2, False, None)  # no STATE yet


def test_typos_are_refused_not_crashing():
    # Each would have ended the tool mid-demo (Dot99, review of #174).
    for words in (["m", "abc"], ["m", "40000"], ["m", "-1001"]):
        try:
            ground_link.parse_roll(words)
            raise AssertionError(words)
        except ValueError:
            pass
    assert ground_link.parse_roll(["m"]) == 300
    assert ground_link.parse_roll(["m", "-1000"]) == -1000
    for words in (["c"], ["c", "zz"], ["c", "100"]):
        try:
            ground_link.parse_key(words)
            raise AssertionError(words)
        except ValueError:
            pass
    assert ground_link.parse_key(["c", "40"]) == 0x40


def test_garbage_twice_in_a_row_never_sends_a_valid_frame():
    # BrunoBMFT, review of #174: with no SETPOINT between two `g` (during `s`),
    # the second replay was a frame the ESP had never seen, so it was accepted.
    link = open_link()
    udp = Receiver(Role.ESP, Transport.UDP)
    udp.start_session(SESSION, link.keys.ground_to_esp)
    assert is_accepted(udp.receive(link.setpoint(0))[0])  # the stream
    for _ in range(3):  # `s`, then `g` three times: no stream in between
        for name, frame in link.garbage():
            result, _ = udp.receive(frame)
            assert not is_accepted(result), name
    assert is_accepted(udp.receive(link.setpoint(0))[0])  # `u`: back on


def test_fc_health_is_read_and_shown():
    # #179: FC_HEALTH (0x81) goes through the same UDP Receiver as STATE.
    from sd_protocol.messages import FcHealth, encode_fc_health

    link = open_link()
    h = FcHealth(0, 0xFF, 0, 0xFFFF, 0xFFFE, 12, 260, 100, 90, 10, 1, 2, 3, 4, 50, 2)
    frame = encode_frame(
        Header(type=MsgType.FC_HEALTH, session_id=SESSION, seq=1),
        encode_fc_health(h),
        link.keys.esp_to_ground,
    )
    got = link.telemetry(frame)
    assert got == h
    line = ground_link.health_line(got)
    assert "fc=LOST" in line and "armed=?" in line and "status=?" in line
    assert "flags=?" in line  # unknown flags never read as 0x00000000
    assert "attitude=>65s" in line and "analog=12ms" in line and "reply=260ms" in line
    assert "timeouts=10" in line and "errors=10" in line  # 1+2+3+4, not the timeouts
    assert "rc_replaced=2" in line
    assert link.state(frame) is None  # replayed: refused; and not a STATE


def test_acks_are_shown_by_name():
    link = open_link()
    ack = encode_frame(
        Header(type=MsgType.ARM_CONTROL_ACK, session_id=SESSION, seq=2),
        bytes([4]),
        link.keys.esp_to_ground,
    )
    assert link.describe_tcp(ack) == "ARM_CONTROL_ACK 4 FC_NOT_ANSWERING"


def test_t_send_us_comes_from_clock_monotonic_raw(monkeypatch):
    # Protocol Spec §1 (#204): NTP can slew CLOCK_MONOTONIC, not RAW.
    import time

    asked = []

    def fake(clock):
        asked.append(clock)
        return 5_000_123_456_789

    monkeypatch.setattr(time, "clock_gettime_ns", fake)
    assert ground_link.now_us() == 5_000_123_456_789 // 1000 & 0xFFFFFFFF
    assert asked == [time.CLOCK_MONOTONIC_RAW]


def test_hello_ack_later_than_100_ms_is_dropped():
    # REQ-SAF-024 (#204): the ESP seeds its late-check offset from the HELLO, so
    # a delayed handshake must not open a session.
    link = ground_link.GroundLink(PSK, nonce=bytes([0x11] * 16))
    link.hello()
    sent = link.hello_sent_us
    assert not link.take_ack(esp_ack(), rx_us=sent + 100_001)
    assert link.late_acks == 1 and link.keys is None
    assert link.take_ack(esp_ack(), rx_us=sent + 100_000)  # exactly 100 ms: ok
    assert link.session_id == SESSION


def test_hello_ack_bound_survives_the_clock_wrap():
    link = ground_link.GroundLink(PSK, nonce=bytes([0x11] * 16))
    link.hello()
    link.hello_sent_us = 0xFFFFFFF0
    assert link.take_ack(esp_ack(), rx_us=0x00000100)  # 272 us across the wrap


def test_hello_ack_without_our_hello_is_dropped():
    assert not ground_link.GroundLink(PSK).take_ack(esp_ack())


class FakeEspServer:
    """A local TCP listener that plays the ESP's handshake side."""

    def __init__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(2)
        self.addr = self.listener.getsockname()
        self.conns = []

    def accept(self):
        conn, _ = self.listener.accept()
        conn.settimeout(2)
        self.conns.append(conn)
        return conn

    def close(self):
        for c in self.conns + [self.listener]:
            c.close()


def poll_until(hs, now, until, step=0.01):
    """Polls like the main loop; each poll must return at once (#205 review)."""
    import time as _time

    while now < until and not hs.done:
        started = _time.monotonic()
        hs.poll(now)
        assert _time.monotonic() - started < 0.05, "poll() blocked the loop"
        now += step
    return now


def test_handshake_never_blocks_and_retries_a_late_ack(monkeypatch):
    # #205 review: the reconnect runs from the loop; the first HELLO_ACK comes
    # 150 ms late (REQ-SAF-024), so a new handshake with a new nonce follows
    # 0.5 s later and opens the session.
    esp = FakeEspServer()
    try:
        link = ground_link.GroundLink(PSK, nonce=bytes([0x11] * 16))
        first_nonce = link.nonce
        hs = ground_link.Handshake(esp.addr, link, 1.0, 0.0)
        now = poll_until(hs, 0.0, 0.05)
        conn = esp.accept()
        now = poll_until(hs, now, now + 0.05)
        assert conn.recv(512)  # the HELLO
        sent = link.hello_sent_us
        monkeypatch.setattr(ground_link, "now_us", lambda: sent + 150_000)
        conn.sendall(esp_ack())
        now = poll_until(hs, now, now + 0.1)
        assert link.late_acks == 1 and not hs.done and hs.tcp is None
        now = poll_until(hs, now, now + 0.3)
        assert hs.tries == 1  # waits 0.5 s before the next HELLO
        now = poll_until(hs, now, now + 0.3)
        conn2 = esp.accept()
        now = poll_until(hs, now, now + 0.05)
        assert conn2.recv(512)
        sent = link.hello_sent_us
        monkeypatch.setattr(ground_link, "now_us", lambda: sent + 20_000)
        conn2.sendall(esp_ack())
        poll_until(hs, now, now + 0.1)
        assert hs.done and hs.result is not None and hs.tries == 2
        assert link.session_id == SESSION and link.nonce != first_nonce
        hs.result[0].close()
    finally:
        esp.close()


def test_no_ack_after_a_late_one_reports_no_hello_ack(monkeypatch):
    # #205 review: a late HELLO_ACK, then none at all, is a missing HELLO_ACK
    # (drone gone, wrong PSK), not a REQ-SAF-024 failure.
    esp = FakeEspServer()
    try:
        link = ground_link.GroundLink(PSK, nonce=bytes([0x11] * 16))
        hs = ground_link.Handshake(esp.addr, link, 0.3, 0.0)
        now = poll_until(hs, 0.0, 0.05)
        conn = esp.accept()
        now = poll_until(hs, now, now + 0.05)
        conn.recv(512)
        sent = link.hello_sent_us
        monkeypatch.setattr(ground_link, "now_us", lambda: sent + 150_000)
        conn.sendall(esp_ack())
        now = poll_until(hs, now, now + 0.6)
        esp.accept()  # second attempt connects, the ESP never answers
        poll_until(hs, now, now + 1.0)
        assert hs.done and hs.result is None
        assert hs.why.startswith("no HELLO_ACK"), hs.why
    finally:
        esp.close()
