import pytest

from sd_protocol import (
    PAYLOAD_SIZES,
    Header,
    MsgType,
    Receiver,
    RxResult,
    Transport,
    encode_frame,
    is_accepted,
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


def open_receiver(transport: Transport = Transport.UDP) -> Receiver:
    rx = Receiver()
    assert rx.start_session(SESSION, TEST_KEY, transport)
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
    rx = Receiver()
    assert not rx.in_session
    assert rx.receive(SEQ1) == (RxResult.NO_SESSION, None)
    assert rx.counters.no_session == 1
    assert rx.counters.rejected == 1


def test_session_id_zero_is_reserved_for_hello():
    rx = Receiver()
    assert not rx.start_session(0, TEST_KEY, Transport.UDP)
    assert not rx.in_session


def test_session_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        Receiver().start_session(SESSION, TEST_KEY[:31], Transport.UDP)


def test_unknown_transport_refused_and_nothing_changes():
    rx = open_receiver()
    with pytest.raises(ValueError):
        rx.start_session(SESSION + 1, TEST_KEY, 2)
    assert (rx.session_id, rx.transport) == (SESSION, Transport.UDP)


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
    rx = Receiver()
    other = bytearray(TEST_KEY)
    other[31] ^= 0xFF
    assert rx.start_session(SESSION, bytes(other), Transport.UDP)
    assert rx.receive(SEQ1)[0] is RxResult.BAD_TAG


def test_new_session_resets_the_window_and_end_session_closes_it():
    rx = open_receiver()
    assert rx.receive(SEQ3)[0] is RxResult.ACCEPTED_GAP
    assert rx.start_session(SESSION, TEST_KEY, Transport.UDP)
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
    rx = open_receiver(transport_of(msg_type))
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
