"""Unit tests of the bench ground (scripts/ground_link.py) against the Python
twin of the ESP-side checks (sd_protocol): no network, no ESP."""

import pathlib
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
