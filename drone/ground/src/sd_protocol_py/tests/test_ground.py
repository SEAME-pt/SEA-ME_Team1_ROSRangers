"""sd_protocol.ground: the ground side of a session without sockets (B2)."""

import json

import pytest

from sd_protocol.crypto import derive_keys
from sd_protocol.frame import Header, MsgType, encode_frame
from sd_protocol.ground import GroundSession, TcpReader, load_psk, t_send_us
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


@pytest.mark.parametrize("content", [None, "not json", "[1, 2]", '{"psk": "12"}'])
def test_load_psk_refuses_missing_or_bad(tmp_path, content):
    path = tmp_path / "secrets.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(ValueError, match="PSK"):
        load_psk(path=path, environ={})


def test_t_send_us_wraps_to_u32():
    assert t_send_us(1.5) == 1_500_000
    assert t_send_us(5000.0) == 5_000_000_000 & 0xFFFFFFFF


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
