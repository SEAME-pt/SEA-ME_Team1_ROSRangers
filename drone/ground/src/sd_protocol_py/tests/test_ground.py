"""sd_protocol.ground: the ground side of a session without sockets (B2)."""

import json
import logging

import pytest

from sd_protocol.crypto import derive_keys
from sd_protocol.frame import HEADER_SIZE, Header, MsgType, decode_frame, encode_frame
from sd_protocol.ground import GroundSession, TcpReader, load_psk
from sd_protocol.messages import (
    EspState,
    HelloAck,
    Role,
    State,
    Transport,
    decode_config_kv,
    decode_hello,
    decode_u8_payload,
    encode_hello_ack,
    encode_state,
)
from sd_protocol.receiver import HandshakeResult, Receiver, receive_handshake
from sd_protocol.setpoint import decode_setpoint

PSK = bytes(range(32))
ESP_NONCE = bytes(16)
SID = 0x1234


def esp_ack(session, psk=PSK):
    """What the ESP answers to the session's HELLO."""
    result, f = receive_handshake(Role.ESP, session.hello(1.0), psk)
    assert result == HandshakeResult.ACCEPTED
    hello = decode_hello(f.payload)
    ack = HelloAck(SID, ESP_NONCE, bytes(12))
    frame = encode_frame(
        Header(MsgType.HELLO_ACK, session_id=SID, seq=1), encode_hello_ack(ack), psk
    )
    return frame, derive_keys(psk, hello.ground_nonce, ESP_NONCE)


def open_session():
    s = GroundSession(PSK)
    frame, keys = esp_ack(s)
    assert s.take_ack(frame) and s.is_open and s.session_id == SID
    esp_tcp = Receiver(Role.ESP, Transport.TCP)
    esp_udp = Receiver(Role.ESP, Transport.UDP)
    esp_tcp.start_session(SID, keys.ground_to_esp)
    esp_udp.start_session(SID, keys.ground_to_esp)
    return s, keys, esp_tcp, esp_udp


def test_load_psk_from_env_then_file(tmp_path):
    key = "ab" * 32
    assert load_psk(environ={"SEAME_PSK": key.upper()}) == bytes.fromhex(key)
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"psk": key}))
    assert load_psk(path=path, environ={}) == bytes.fromhex(key)


# ---------- decision E3: a secrets file other users can read ----------
@pytest.mark.parametrize(
    "mode, warned",
    [
        (0o600, False),
        (0o400, False),
        (0o700, False),
        (0o640, True),
        (0o604, True),
        (0o644, True),
    ],
)
def test_load_psk_warns_when_other_users_can_read_the_file(tmp_path, mode, warned):
    key = "cd" * 32
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"psk": key}))
    path.chmod(mode)
    messages = []
    assert load_psk(path=path, environ={}, warn=messages.append) == bytes.fromhex(key)
    assert len(messages) == (1 if warned else 0)
    if warned:  # it says which file, its mode and the fix
        assert str(path) in messages[0] and f"mode {mode:o}" in messages[0]
        assert f"chmod 600 {path}" in messages[0]


def test_load_psk_from_the_environment_never_reads_the_file(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"psk": "cd" * 32}))
    path.chmod(0o644)
    messages = []
    load_psk(path=path, environ={"SEAME_PSK": "ab" * 32}, warn=messages.append)
    assert messages == []


def test_load_psk_warning_goes_to_the_log_without_a_callback(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"psk": "cd" * 32}))
    path.chmod(0o644)
    # A handler on the module's own logger, not caplog: with ROS sourced the
    # launch pytest plugin changes the logger class and stops propagation.
    records = []
    handler = logging.Handler(level=logging.WARNING)
    handler.emit = records.append
    logger = logging.getLogger("sd_protocol.ground")
    logger.addHandler(handler)
    try:
        load_psk(path=path, environ={})
    finally:
        logger.removeHandler(handler)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert "chmod 600" in records[0].getMessage()


@pytest.mark.parametrize("content", [None, "not json", "[1, 2]", '{"psk": "12"}'])
def test_load_psk_refuses_missing_or_bad(tmp_path, content):
    path = tmp_path / "secrets.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(ValueError, match="PSK"):
        load_psk(path=path, environ={})


def test_the_fallback_clock_wraps_to_u32(monkeypatch):
    # Without CLOCK_MONOTONIC_RAW (not Linux): the monotonic clock, as a u32.
    import time

    from sd_protocol import ground

    monkeypatch.setattr(ground, "_RAW", None)
    monkeypatch.setattr(time, "monotonic", lambda: 5000.0)
    assert ground.raw_now_us() == 5_000_000_000 & 0xFFFFFFFF


def test_ack_with_another_psk_or_garbage_is_refused():
    s = GroundSession(PSK)
    frame, _ = esp_ack(s)
    other = GroundSession(bytes(32))
    assert not other.take_ack(frame) and not other.is_open
    assert not s.take_ack(b"garbage")


def test_sealed_frames_pass_the_esp_receivers():
    s, _, esp_tcp, esp_udp = open_session()
    _, f = esp_udp.receive(s.setpoint(2.0, mode=1, roll=100))
    assert decode_setpoint(f.payload).roll == 100
    _, f = esp_udp.receive(s.neutral(2.0))
    assert decode_setpoint(f.payload).mode == 0
    _, f = esp_tcp.receive(s.arm(8, 2.0))
    assert f.header.type == MsgType.ARM_CONTROL_REQUEST
    assert decode_u8_payload(f.payload) == 8
    _, f = esp_tcp.receive(s.config_get(0x01, 2.0))
    assert decode_config_kv(f.payload).key == 0x01
    _, f = esp_tcp.receive(s.resume_request(2.0))
    assert f.header.type == MsgType.RESUME_REQUEST
    _, f = esp_tcp.receive(s.bye(2.0))
    assert decode_u8_payload(f.payload) == 0


def test_receive_checks_esp_frames():
    s, keys, _, _ = open_session()
    state = State(int(EspState.SESSION_IDLE), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 0)
    frame = encode_frame(
        Header(MsgType.STATE, session_id=SID, seq=1),
        encode_state(state),
        keys.esp_to_ground,
    )
    assert s.receive_udp_state(frame).esp_state == EspState.SESSION_IDLE
    assert s.receive_udp_state(frame) is None  # replayed: refused
    other = encode_frame(
        Header(0x81, session_id=SID, seq=2), bytes(4), keys.esp_to_ground
    )
    assert s.receive_udp_state(other) is None  # not a STATE
    bye = encode_frame(
        Header(MsgType.BYE, session_id=SID, seq=2), b"\x03", keys.esp_to_ground
    )
    assert s.receive_tcp(bye).header.type == MsgType.BYE
    assert s.receive_tcp(bye) is None
    assert s.rejected >= 2


def test_tcp_reader_cuts_frames_and_refuses_a_bad_length():
    reader = TcpReader()
    hello = GroundSession(PSK).hello(0.0)
    assert reader.feed(hello[:10]) == []
    assert reader.feed(hello[10:] + hello) == [hello, hello]
    with pytest.raises(ValueError):
        reader.feed(b"SD\x01\x40\x00\x00\xff\xff" + bytes(12))


def test_tcp_reader_waits_for_a_frame_cut_after_its_header():
    """TCP can cut anywhere, also inside the payload or the tag."""
    s, *_ = open_session()
    a, b = s.config_get(1, 0.0), s.bye(0.0)
    reader = TcpReader()
    assert reader.feed(a[:-1]) == []  # the header is complete, the tag is not
    assert reader.feed(a[-1:] + b[: HEADER_SIZE + 1]) == [a]
    assert reader.feed(b[HEADER_SIZE + 1 :]) == [b]
    assert reader.buf == b""


# ---------- #204: t_send_us from CLOCK_MONOTONIC_RAW, REQ-SAF-024 ----------
class FakeClock:
    def __init__(self, us=1_000_000):
        self.us = us

    def __call__(self):
        return self.us & 0xFFFFFFFF


def test_frames_carry_the_raw_clock_not_now():
    clock = FakeClock(7_000_000)
    s = GroundSession(PSK, clock_us=clock)
    frame, _ = esp_ack(s)  # HELLO at 7.000 s
    assert s.take_ack(frame)
    clock.us = 7_020_000
    sp = s.setpoint(123.0)  # `now` is the caller's time, not the stamp
    _, f = decode_frame(sp, s.keys.ground_to_esp)
    assert f.header.t_send_us == 7_020_000


def test_raw_now_us_reads_clock_monotonic_raw(monkeypatch):
    import time

    from sd_protocol import ground

    asked = []

    def fake(clock):
        asked.append(clock)
        return 5_000_123_456_789

    monkeypatch.setattr(time, "clock_gettime_ns", fake)
    assert ground.raw_now_us() == 5_000_123_456_789 // 1000 & 0xFFFFFFFF
    assert asked == [time.CLOCK_MONOTONIC_RAW]


@pytest.mark.parametrize("waited_us, opened", [(100_000, True), (100_001, False)])
def test_hello_ack_later_than_100_ms_is_refused(waited_us, opened):
    clock = FakeClock()
    s = GroundSession(PSK, clock_us=clock)
    frame, _ = esp_ack(s)
    clock.us += waited_us
    assert s.take_ack(frame) is opened
    assert s.is_open is opened
    assert s.late_acks == (0 if opened else 1)


def test_hello_ack_bound_survives_the_clock_wrap():
    clock = FakeClock(0xFFFFFFF0)
    s = GroundSession(PSK, clock_us=clock)
    frame, _ = esp_ack(s)
    clock.us = 0x1_0000_0100  # 272 us later, across the u32 wrap
    assert s.take_ack(frame)


def test_hello_ack_without_our_hello_is_refused():
    s = GroundSession(PSK)
    frame, _ = esp_ack(GroundSession(PSK))  # another session's HELLO
    assert not s.take_ack(frame) and not s.is_open


def test_take_ack_can_be_given_the_arrival_time():
    # #229: a caller that knows when the frame arrived (a test, a log) passes
    # it; the 100 ms bound is then measured to that time.
    clock = FakeClock()
    s = GroundSession(PSK, clock_us=clock)
    frame, _ = esp_ack(s)
    sent = s.hello_sent_us
    assert not s.take_ack(frame, rx_us=sent + 100_001) and s.late_acks == 1
    assert s.take_ack(frame, rx_us=sent + 100_000)


def test_take_ack_keeps_the_esps_firmware_name():
    s = GroundSession(PSK)
    assert s.fw_version == ""
    s.hello(0.0)
    ack = HelloAck(SID, ESP_NONCE, b"sd-gw 0.2.0L")
    frame = encode_frame(
        Header(MsgType.HELLO_ACK, session_id=SID, seq=1), encode_hello_ack(ack), PSK
    )
    assert s.take_ack(frame)
    assert s.fw_version == "sd-gw 0.2.0L"
