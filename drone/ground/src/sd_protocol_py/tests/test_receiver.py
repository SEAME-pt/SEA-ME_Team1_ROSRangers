import pytest

from sd_protocol import (
    HELLO_ACK_SIZE,
    HELLO_SIZE,
    PAYLOAD_SIZES,
    HandshakeResult,
    Header,
    HelloAck,
    MsgType,
    Receiver,
    Role,
    RxResult,
    Transport,
    encode_frame,
    encode_hello_ack,
    is_accepted,
    receive_handshake,
    receives,
    transport_of,
)

# Same cases as drone/lib/protocol/tests/test_receiver.cpp.

TEST_KEY = bytes(range(32))  # spec §6 test key, never the real PSK
SESSION = 0x12345678

# From drone/tests/vectors/README.md: neutral setpoints with seq 1, 2, 3, and seq 7.
SEQ1 = bytes.fromhex(
    "5344016000000a007856341201000000204e0000010000000000000000006c870020e780aa97"
)
SEQ2 = bytes.fromhex(
    "5344016000000a007856341202000000409c000001000000000000000000712720248f2ea86c"
)
SEQ3 = bytes.fromhex(
    "5344016000000a00785634120300000060ea00000100000000000000000074f74adcb71e36f6"
)
SEQ7 = bytes.fromhex(
    "5344016000000a007856341207000000e803000001640038ff0000f40100fb703d824f5b589e"
)
# Same as SEQ7 but session 0x12345679, tag valid.
OTHER_SESSION = bytes.fromhex(
    "5344016000000a007956341207000000e803000001640038ff0000f40100b6785492183eae61"
)


def open_receiver(
    transport: Transport = Transport.UDP, role: Role = Role.ESP
) -> Receiver:
    rx = Receiver(role, transport)
    assert rx.start_session(SESSION, TEST_KEY)
    return rx


def setpoint(session: int, seq: int) -> bytes:
    """A neutral SETPOINT (UDP) with any session and seq."""
    return encode_frame(
        Header(MsgType.SETPOINT, 0, session, seq), SEQ1[20:30], TEST_KEY
    )


def resume_request(session: int, seq: int) -> bytes:
    """A RESUME_REQUEST (TCP, empty payload)."""
    return encode_frame(Header(MsgType.RESUME_REQUEST, 0, session, seq), b"", TEST_KEY)


def test_nothing_is_accepted_before_a_session():
    rx = Receiver(Role.ESP, Transport.UDP)
    assert not rx.in_session
    assert rx.receive(SEQ1) == (RxResult.NO_SESSION, None)
    assert rx.counters.no_session == 1
    assert rx.counters.rejected == 1


def test_session_id_zero_is_reserved_for_hello():
    rx = Receiver(Role.ESP, Transport.UDP)
    assert not rx.start_session(0, TEST_KEY)
    assert not rx.in_session


def test_session_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        Receiver(Role.ESP, Transport.UDP).start_session(SESSION, TEST_KEY[:31])


def test_unknown_transport_refused_and_nothing_changes():
    with pytest.raises(ValueError):
        Receiver(Role.ESP, 2)


def test_unknown_role_refused():
    with pytest.raises(ValueError):
        Receiver(2, Transport.UDP)


def test_refused_start_keeps_the_open_session():
    rx = open_receiver()
    assert not rx.start_session(0, TEST_KEY)
    with pytest.raises(ValueError):
        rx.start_session(SESSION + 1, TEST_KEY[:31])
    assert (rx.in_session, rx.session_id) == (True, SESSION)
    assert rx.receive(SEQ1)[0] is RxResult.ACCEPTED


def test_keeps_its_role_and_transport():
    rx = Receiver(Role.GROUND, Transport.TCP)
    assert (rx.role, rx.transport) == (Role.GROUND, Transport.TCP)


def test_accepts_a_valid_frame_and_returns_its_fields():
    rx = open_receiver()
    result, frame = rx.receive(SEQ7)
    assert result is RxResult.ACCEPTED_GAP
    assert frame.header.type == MsgType.SETPOINT
    assert frame.header.seq == 7
    assert rx.last_seq == 7
    assert rx.counters.accepted == 1


# Vector group 4 (session).
def test_wrong_session_rejected_even_with_a_valid_tag():
    rx = open_receiver()
    assert rx.receive(OTHER_SESSION) == (RxResult.WRONG_SESSION, None)
    assert rx.counters.wrong_session == 1
    assert rx.last_seq == 0  # a rejected frame never moves the window


def test_session_id_zero_outside_hello_rejected():
    rx = open_receiver()
    assert rx.receive(setpoint(0, 1))[0] is RxResult.WRONG_SESSION


# Vector group 5 (sequence): the seq_duplicate example from the README.
def test_seq_duplicate_example():
    rx = open_receiver()
    assert rx.receive(SEQ1)[0] is RxResult.ACCEPTED
    assert rx.receive(SEQ2)[0] is RxResult.ACCEPTED
    assert rx.receive(SEQ2) == (RxResult.DUPLICATE_SEQ, None)
    assert rx.receive(SEQ3)[0] is RxResult.ACCEPTED
    assert rx.last_seq == 3
    assert rx.counters.rejected == 1
    assert rx.counters.duplicate_seq == 1


def test_older_seq_zero_and_too_high_rejected():
    rx = open_receiver()
    assert rx.receive(SEQ7)[0] is RxResult.ACCEPTED_GAP
    assert rx.receive(SEQ3)[0] is RxResult.OLD_SEQ
    assert rx.receive(setpoint(SESSION, 0))[0] is RxResult.OLD_SEQ
    assert rx.receive(setpoint(SESSION, 0x80000000))[0] is RxResult.SEQ_TOO_HIGH
    assert rx.counters.old_seq == 2
    assert rx.counters.seq_too_high == 1
    assert rx.seq_counters.gaps == 1


def test_every_frame_reject_is_counted():
    rx = open_receiver()
    assert rx.receive(SEQ1[:5])[0] is RxResult.TRUNCATED
    bad = bytearray(SEQ1)
    bad[0] = 0
    assert rx.receive(bytes(bad))[0] is RxResult.BAD_MAGIC
    bad = bytearray(SEQ1)
    bad[2] = 9
    assert rx.receive(bytes(bad))[0] is RxResult.BAD_VERSION
    assert rx.receive(SEQ1 + b"\x00")[0] is RxResult.BAD_LENGTH
    bad = bytearray(SEQ1)
    bad[25] ^= 1
    assert rx.receive(bytes(bad))[0] is RxResult.BAD_TAG

    c = rx.counters
    assert (c.truncated, c.bad_magic, c.bad_version, c.bad_length, c.bad_tag) == (
        1,
        1,
        1,
        1,
        1,
    )
    assert c.rejected == 5
    assert c.accepted == 0

    # The good frame is still accepted afterwards: rejects leave no state behind.
    assert rx.receive(SEQ1)[0] is RxResult.ACCEPTED


def test_wrong_key_rejected():
    rx = Receiver(Role.ESP, Transport.UDP)
    other = bytearray(TEST_KEY)
    other[31] ^= 0xFF
    assert rx.start_session(SESSION, bytes(other))
    assert rx.receive(SEQ1)[0] is RxResult.BAD_TAG


def test_new_session_resets_the_window_and_end_session_closes_it():
    rx = open_receiver()
    assert rx.receive(SEQ3)[0] is RxResult.ACCEPTED_GAP
    assert rx.start_session(SESSION, TEST_KEY)
    assert rx.last_seq == 0
    assert rx.receive(SEQ1)[0] is RxResult.ACCEPTED

    rx.end_session()
    assert not rx.in_session
    assert rx.session_id == 0
    assert rx.receive(SEQ2)[0] is RxResult.NO_SESSION


def test_is_accepted():
    assert is_accepted(RxResult.ACCEPTED)
    assert is_accepted(RxResult.ACCEPTED_GAP)
    assert not is_accepted(RxResult.BAD_TAG)
    assert not is_accepted(RxResult.DUPLICATE_SEQ)


def test_unknown_type_rejected_and_seq_not_used():
    rx = open_receiver()
    unknown = encode_frame(Header(0x99, 0, SESSION, 1), b"", TEST_KEY)
    assert rx.receive(unknown) == (RxResult.BAD_TYPE, None)
    assert (rx.counters.bad_type, rx.counters.rejected, rx.last_seq) == (1, 1, 0)
    assert rx.receive(SEQ1)[0] is RxResult.ACCEPTED  # seq 1 still free


def test_payload_size_must_match_the_type():
    rx = open_receiver()
    short = encode_frame(Header(MsgType.SETPOINT, 0, SESSION, 1), bytes(9), TEST_KEY)
    assert rx.receive(short) == (RxResult.BAD_PAYLOAD_SIZE, None)
    assert (rx.counters.bad_payload_size, rx.counters.rejected, rx.last_seq) == (
        1,
        1,
        0,
    )
    assert rx.receive(SEQ1)[0] is RxResult.ACCEPTED  # seq 1 still free


@pytest.mark.parametrize(
    "msg_type, size, ok",
    [
        (MsgType.RESUME_REQUEST, 0, True),
        (MsgType.RESUME_REQUEST, 1, False),
        (MsgType.EVENT, 1, False),
        (MsgType.EVENT, 2, True),
        (MsgType.EVENT, 66, True),
        (MsgType.EVENT, 67, False),
        (MsgType.STATE, 28, True),
        (MsgType.STATE, 27, False),
    ],
)
def test_payload_size_bounds(msg_type, size, ok):
    role = Role.ESP if receives(Role.ESP, msg_type) else Role.GROUND
    rx = open_receiver(transport_of(msg_type), role)
    buf = encode_frame(Header(msg_type, 0, SESSION, 2), bytes(size), TEST_KEY)
    assert is_accepted(rx.receive(buf)[0]) is ok


def test_every_type_has_a_payload_size():
    assert set(PAYLOAD_SIZES) == set(MsgType)


def test_setpoint_mode_other_than_0_or_1_rejected_before_the_seq_check():
    # Spec §4 rule 4 (v1.1): bad_mode, and the seq stays free (REQ-COM-004).
    rx = open_receiver()
    payload = bytearray(SEQ1[20:30])
    payload[0] = 2
    bad = encode_frame(
        Header(MsgType.SETPOINT, 0, SESSION, 1), bytes(payload), TEST_KEY
    )
    assert rx.receive(bad) == (RxResult.BAD_MODE, None)
    assert (rx.counters.bad_mode, rx.counters.rejected, rx.last_seq) == (1, 1, 0)
    assert rx.receive(SEQ1)[0] is RxResult.ACCEPTED  # same seq, now accepted


# Spec §2 (v1.1.1): each Receiver accepts only the types of its transport.
def test_tcp_window_starts_at_1_after_hello():
    tcp = open_receiver(Transport.TCP)
    assert tcp.transport is Transport.TCP
    assert tcp.last_seq == 1
    assert tcp.receive(resume_request(SESSION, 1)) == (RxResult.DUPLICATE_SEQ, None)
    assert tcp.receive(resume_request(SESSION, 2))[0] is RxResult.ACCEPTED


def test_frame_on_the_wrong_transport_rejected_and_seq_not_used():
    udp, tcp = open_receiver(Transport.UDP), open_receiver(Transport.TCP)
    assert udp.receive(resume_request(SESSION, 2)) == (RxResult.WRONG_TRANSPORT, None)
    assert tcp.receive(SEQ7) == (RxResult.WRONG_TRANSPORT, None)
    assert (udp.counters.wrong_transport, udp.counters.rejected, udp.last_seq) == (
        1,
        1,
        0,
    )
    assert (tcp.counters.wrong_transport, tcp.counters.rejected, tcp.last_seq) == (
        1,
        1,
        1,
    )
    assert udp.receive(SEQ7)[0] is RxResult.ACCEPTED_GAP
    assert tcp.receive(resume_request(SESSION, 2))[0] is RxResult.ACCEPTED


def test_unknown_type_is_bad_type_not_wrong_transport():
    tcp = open_receiver(Transport.TCP)
    unknown = encode_frame(Header(0xA0, 0, SESSION, 2), b"", TEST_KEY)
    assert tcp.receive(unknown)[0] is RxResult.BAD_TYPE


# #152, spec §2: each side accepts only what the other side sends, even with the
# same key on both ends (OwnFramesRejectedEvenWithTheSameKey in test_receiver.cpp).
def frame_of(msg_type: MsgType, payload: bytes, seq: int) -> bytes:
    return encode_frame(Header(msg_type, 0, SESSION, seq), payload, TEST_KEY)


def test_own_frames_rejected_even_with_the_same_key():
    esp, ground = open_receiver(), open_receiver(role=Role.GROUND)
    esp_tcp = open_receiver(Transport.TCP)
    ground_tcp = open_receiver(Transport.TCP, Role.GROUND)

    state = frame_of(MsgType.STATE, bytes(28), 100)
    setpoint_frame = frame_of(MsgType.SETPOINT, SEQ1[20:30], 100)
    assert esp.receive(state) == (RxResult.WRONG_DIRECTION, None)
    assert ground.receive(setpoint_frame) == (RxResult.WRONG_DIRECTION, None)
    ack = frame_of(MsgType.ARM_CONTROL_ACK, b"\x00", 100)
    assert esp_tcp.receive(ack)[0] is RxResult.WRONG_DIRECTION
    request = frame_of(MsgType.ARM_CONTROL_REQUEST, b"\x0f", 100)
    assert ground_tcp.receive(request)[0] is RxResult.WRONG_DIRECTION
    # Refused before the window: no seq used.
    assert [r.last_seq for r in (esp, ground, esp_tcp, ground_tcp)] == [0, 0, 1, 1]
    assert (esp.counters.wrong_direction, esp.counters.rejected) == (1, 1)
    assert ground_tcp.counters.wrong_direction == 1

    bye = frame_of(MsgType.BYE, b"\x00", 2)  # BYE goes both ways
    assert esp_tcp.receive(bye)[0] is RxResult.ACCEPTED
    assert ground_tcp.receive(bye)[0] is RxResult.ACCEPTED
    # The real traffic still goes through.
    assert esp.receive(setpoint_frame)[0] is RxResult.ACCEPTED_GAP
    assert ground.receive(state)[0] is RxResult.ACCEPTED_GAP


def test_direction_is_checked_after_transport_and_before_size():
    esp = open_receiver()
    # A ground-side TCP type on the ESP UDP Receiver: transport first.
    assert esp.receive(frame_of(MsgType.ARM_CONTROL_ACK, b"\x00", 1))[0] is (
        RxResult.WRONG_TRANSPORT
    )
    # STATE with a wrong size on the ESP: direction before size.
    assert esp.receive(frame_of(MsgType.STATE, bytes(3), 1))[0] is (
        RxResult.WRONG_DIRECTION
    )


def test_hello_on_a_receiver_is_wrong_direction():
    # Every HELLO goes to receive_handshake(), also during a session.
    tcp = open_receiver(Transport.TCP)
    hello = frame_of(MsgType.HELLO, bytes(HELLO_SIZE), 2)
    assert tcp.receive(hello)[0] is RxResult.WRONG_DIRECTION


# Spec §1, #152 (Handshake.EachSideAcceptsOnlyItsOwnHandshakeFrame in test_receiver.cpp).
def handshake(msg_type: MsgType, session: int, seq: int, payload: bytes) -> bytes:
    """A handshake frame tagged with the PSK (TEST_KEY), any header values."""
    return encode_frame(Header(msg_type, 0, session, seq), payload, TEST_KEY)


def hello_ack_payload(session: int) -> bytes:
    return encode_hello_ack(HelloAck(session, bytes(16), bytes(12)))


HELLO_PAYLOAD = b"\x11" * HELLO_SIZE
HELLO = handshake(MsgType.HELLO, 0, 1, HELLO_PAYLOAD)
HELLO_ACK = handshake(MsgType.HELLO_ACK, SESSION, 1, hello_ack_payload(SESSION))


@pytest.mark.parametrize(
    "role, buf, expected",
    [
        (Role.ESP, HELLO, HandshakeResult.ACCEPTED),
        (Role.GROUND, HELLO_ACK, HandshakeResult.ACCEPTED),
        # Reflected or out of place: each side refuses the other's frame.
        (Role.GROUND, HELLO, HandshakeResult.WRONG_DIRECTION),
        (Role.ESP, HELLO_ACK, HandshakeResult.WRONG_DIRECTION),
        # Any other type tagged with the PSK.
        (
            Role.ESP,
            handshake(MsgType.SETPOINT, 0, 1, bytes(10)),
            HandshakeResult.WRONG_DIRECTION,
        ),
        (
            Role.ESP,
            handshake(MsgType.HELLO, 5, 1, HELLO_PAYLOAD),
            HandshakeResult.WRONG_SESSION,
        ),
        (
            Role.GROUND,
            handshake(MsgType.HELLO_ACK, 0, 1, hello_ack_payload(0)),
            HandshakeResult.WRONG_SESSION,
        ),
        (
            Role.GROUND,
            handshake(MsgType.HELLO_ACK, SESSION, 1, hello_ack_payload(SESSION + 1)),
            HandshakeResult.WRONG_SESSION,
        ),
        (
            Role.ESP,
            handshake(MsgType.HELLO, 0, 2, HELLO_PAYLOAD),
            HandshakeResult.BAD_SEQ,
        ),
        (
            Role.ESP,
            handshake(MsgType.HELLO, 0, 1, bytes(HELLO_SIZE - 1)),
            HandshakeResult.BAD_PAYLOAD_SIZE,
        ),
        (
            Role.GROUND,
            handshake(MsgType.HELLO_ACK, SESSION, 1, bytes(HELLO_ACK_SIZE + 1)),
            HandshakeResult.BAD_PAYLOAD_SIZE,
        ),
        # Wrong key, broken frame.
        (Role.ESP, HELLO[:-1] + bytes([HELLO[-1] ^ 1]), HandshakeResult.BAD_TAG),
        (Role.ESP, HELLO[:10], HandshakeResult.TRUNCATED),
        (Role.ESP, b"X" + HELLO[1:], HandshakeResult.BAD_MAGIC),
        (Role.ESP, HELLO[:2] + b"\x09" + HELLO[3:], HandshakeResult.BAD_VERSION),
        (Role.ESP, HELLO + b"\x00", HandshakeResult.BAD_LENGTH),
    ],
)
def test_each_side_accepts_only_its_own_handshake_frame(role, buf, expected):
    result, frame = receive_handshake(role, buf, TEST_KEY)
    assert result is expected
    # The frame comes back only when accepted.
    assert (frame is None) is (expected is not HandshakeResult.ACCEPTED)


def test_accepted_handshake_returns_the_frame():
    result, frame = receive_handshake(Role.GROUND, HELLO_ACK, TEST_KEY)
    assert result is HandshakeResult.ACCEPTED
    assert (frame.header.type, frame.header.session_id) == (MsgType.HELLO_ACK, SESSION)


def test_handshake_with_the_wrong_key_or_role_refused():
    assert receive_handshake(Role.ESP, HELLO, bytes(32))[0] is HandshakeResult.BAD_TAG
    with pytest.raises(ValueError):
        receive_handshake(2, HELLO, TEST_KEY)
