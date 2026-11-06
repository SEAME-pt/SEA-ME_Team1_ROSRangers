import pytest

from sd_protocol import Header, MsgType, RxError, decode_frame, encode_frame, frame_size
from sd_protocol.frame import HEADER_SIZE, MAX_FRAME, MAX_PAYLOAD, TAG_SIZE

TEST_KEY = bytes(range(32))  # spec §6 test key, never the real PSK
OTHER_KEY = bytes(range(1, 33))
SESSION = 0x12345678

# Same reference frame as drone/lib/protocol/tests/test_frame.cpp (kSetpointBasic):
# SETPOINT mode 1, roll 100, pitch -200, yaw 0, throttle 500, seq 7, t_send 1000.
SETPOINT_BASIC = bytes.fromhex(
    "5344016000000a007856341207000000e803000001640038ff0000f40100fb703d824f5b589e"
)
SETPOINT_PAYLOAD = SETPOINT_BASIC[HEADER_SIZE:-TAG_SIZE]
SETPOINT_HEADER = Header(MsgType.SETPOINT, 0, SESSION, 7, 1000)


def test_encodes_the_cpp_reference_setpoint_byte_for_byte():
    assert encode_frame(SETPOINT_HEADER, SETPOINT_PAYLOAD, TEST_KEY) == SETPOINT_BASIC


def test_decodes_the_cpp_reference_setpoint():
    error, frame = decode_frame(SETPOINT_BASIC, TEST_KEY)
    assert error is RxError.OK
    assert frame.header == SETPOINT_HEADER
    assert frame.payload == SETPOINT_PAYLOAD


def test_header_layout_is_little_endian():
    out = encode_frame(
        Header(0x80, 0x01, 0x04030201, 0x08070605, 0x0C0B0A09), b"\xaa" * 3, TEST_KEY
    )
    assert out[:HEADER_SIZE].hex() == "534401800100030001020304050607080" + "90a0b0c"
    assert len(out) == HEADER_SIZE + 3 + TAG_SIZE


def test_empty_payload_round_trip():
    out = encode_frame(Header(MsgType.RESUME_REQUEST, 0, SESSION, 3, 0), b"", TEST_KEY)
    assert len(out) == HEADER_SIZE + TAG_SIZE
    error, frame = decode_frame(out, TEST_KEY)
    assert error is RxError.OK
    assert frame.header.type == MsgType.RESUME_REQUEST
    assert frame.payload == b""


def test_max_payload_round_trip():
    payload = bytes(i % 256 for i in range(MAX_PAYLOAD))
    out = encode_frame(Header(MsgType.EVENT, 0, SESSION, 1), payload, TEST_KEY)
    assert len(out) == MAX_FRAME
    assert decode_frame(out, TEST_KEY)[1].payload == payload


def test_encode_rejects_a_payload_over_the_limit():
    with pytest.raises(ValueError):
        encode_frame(SETPOINT_HEADER, bytes(MAX_PAYLOAD + 1), TEST_KEY)


def test_encode_rejects_a_key_of_the_wrong_size():
    with pytest.raises(ValueError):
        encode_frame(SETPOINT_HEADER, SETPOINT_PAYLOAD, bytes(16))


def test_every_flipped_bit_is_rejected():
    for i in range(len(SETPOINT_BASIC) * 8):
        bad = bytearray(SETPOINT_BASIC)
        bad[i // 8] ^= 1 << (i % 8)
        error, frame = decode_frame(bytes(bad), TEST_KEY)
        assert error is not RxError.OK, f"bit {i} was accepted"
        assert frame is None


def test_wrong_key_is_a_bad_tag():
    assert decode_frame(SETPOINT_BASIC, OTHER_KEY) == (RxError.BAD_TAG, None)


@pytest.mark.parametrize("length", [0, 1, HEADER_SIZE, HEADER_SIZE + TAG_SIZE - 1])
def test_shorter_than_header_and_tag_is_truncated(length):
    assert decode_frame(SETPOINT_BASIC[:length], TEST_KEY)[0] is RxError.TRUNCATED


def test_shorter_than_payload_len_is_truncated():
    assert decode_frame(SETPOINT_BASIC[:-1], TEST_KEY)[0] is RxError.TRUNCATED


def test_bytes_after_the_frame_are_a_bad_length():
    assert decode_frame(SETPOINT_BASIC + b"\x00", TEST_KEY)[0] is RxError.BAD_LENGTH


def test_payload_len_over_the_limit_is_a_bad_length():
    bad = bytearray(SETPOINT_BASIC)
    bad[6:8] = (MAX_PAYLOAD + 1).to_bytes(2, "little")
    assert decode_frame(bytes(bad), TEST_KEY)[0] is RxError.BAD_LENGTH


def test_checks_run_in_spec_order():
    bad = bytearray(SETPOINT_BASIC)
    bad[0] = 0x00  # bad magic
    bad[2] = 0x09  # and bad version
    assert decode_frame(bytes(bad), TEST_KEY)[0] is RxError.BAD_MAGIC
    bad[0] = 0x53
    assert decode_frame(bytes(bad), TEST_KEY)[0] is RxError.BAD_VERSION


def test_version_zero_is_rejected():
    bad = bytearray(SETPOINT_BASIC)
    bad[2] = 0
    assert decode_frame(bytes(bad), TEST_KEY)[0] is RxError.BAD_VERSION


def test_decode_accepts_bytearray_and_memoryview():
    assert decode_frame(bytearray(SETPOINT_BASIC), TEST_KEY)[0] is RxError.OK
    assert decode_frame(memoryview(SETPOINT_BASIC), TEST_KEY)[0] is RxError.OK


def test_frame_size_from_the_header():
    assert frame_size(SETPOINT_BASIC[:HEADER_SIZE]) == len(SETPOINT_BASIC)
    assert frame_size(SETPOINT_BASIC) == len(SETPOINT_BASIC)


def test_frame_size_needs_a_whole_header():
    assert frame_size(SETPOINT_BASIC[: HEADER_SIZE - 1]) == 0


def test_frame_size_rejects_payload_len_over_the_limit():
    bad = bytearray(SETPOINT_BASIC[:HEADER_SIZE])
    bad[6:8] = (MAX_PAYLOAD + 1).to_bytes(2, "little")
    assert frame_size(bytes(bad)) == 0


def test_frame_size_cuts_a_tcp_stream_into_frames():
    second = encode_frame(Header(MsgType.BYE, 0, SESSION, 8), b"\x00", TEST_KEY)
    stream = SETPOINT_BASIC + second
    first_len = frame_size(stream)
    assert stream[:first_len] == SETPOINT_BASIC
    assert stream[first_len : first_len + frame_size(stream[first_len:])] == second


def test_decode_needs_a_32_byte_key():
    # Same contract as encode_frame: a wrong key length is a caller bug.
    for key in (b"", TEST_KEY[:31], TEST_KEY + b"\x00"):
        with pytest.raises(ValueError):
            decode_frame(SETPOINT_BASIC, key)
    # The key is checked before the frame: even an empty buffer raises.
    with pytest.raises(ValueError):
        decode_frame(b"", b"")
