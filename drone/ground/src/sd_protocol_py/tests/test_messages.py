import pytest

from sd_protocol import (
    CONFIG_ACK_SIZE,
    CONFIG_KV_SIZE,
    EVENT_MAX_SIZE,
    EVENT_MIN_SIZE,
    EVENT_TEXT_MAX,
    FW_VERSION_SIZE,
    HELLO_ACK_SIZE,
    HELLO_SIZE,
    NONCE_SIZE,
    STATE_SIZE,
    AckResult,
    ByeReason,
    ConfigAck,
    ConfigKv,
    ConfigResult,
    EspState,
    Event,
    EventCode,
    Hello,
    HelloAck,
    State,
    decode_config_ack,
    decode_config_kv,
    decode_event,
    decode_hello,
    decode_hello_ack,
    decode_state,
    decode_u8_payload,
    encode_config_ack,
    encode_config_kv,
    encode_event,
    encode_hello,
    encode_hello_ack,
    encode_state,
    encode_u8_payload,
)

# Same cases and expected bytes as drone/lib/protocol/tests/test_messages.cpp.


def test_code_values_match_the_cpp():
    # Suggested in #142, not frozen: a change here must change messages.hpp too.
    assert [int(v) for v in AckResult] == [0, 1, 2, 3]
    assert [int(v) for v in ByeReason] == [0, 1, 2, 3]
    assert [int(v) for v in ConfigResult] == [0, 1, 2, 3, 4]
    assert [int(v) for v in EspState] == [0, 1, 2, 3, 4]
    assert [int(v) for v in EventCode] == [0x0101, 0x0102, 0x0103]


def test_sizes_match_the_spec():
    assert (HELLO_SIZE, HELLO_ACK_SIZE, CONFIG_KV_SIZE, CONFIG_ACK_SIZE) == (
        17,
        32,
        5,
        6,
    )
    assert (EVENT_MIN_SIZE, EVENT_MAX_SIZE, STATE_SIZE) == (2, 66, 28)


def test_hello_bytes_and_round_trip():
    m = Hello(1, bytes(range(0xA0, 0xA0 + NONCE_SIZE)))
    buf = encode_hello(m)
    assert len(buf) == HELLO_SIZE
    assert (buf[0], buf[1], buf[16]) == (1, 0xA0, 0xAF)
    assert decode_hello(buf) == m


def test_hello_ack_bytes_and_round_trip():
    nonce = bytearray(NONCE_SIZE)
    nonce[0], nonce[15] = 0xEE, 0xFF
    m = HelloAck(0x11223344, bytes(nonce), b"v0.1".ljust(FW_VERSION_SIZE, b"\0"))
    buf = encode_hello_ack(m)
    assert len(buf) == HELLO_ACK_SIZE
    assert buf[:4] == bytes([0x44, 0x33, 0x22, 0x11])
    assert (buf[4], buf[19], buf[20], buf[31]) == (0xEE, 0xFF, ord("v"), 0)
    assert decode_hello_ack(buf) == m


def test_u8_payload_round_trip():
    buf = encode_u8_payload(ByeReason.REPLACED)
    assert buf == b"\x03"
    assert decode_u8_payload(buf) == 3


def test_config_kv_bytes_and_round_trip():
    m = ConfigKv(0x80, -2)
    buf = encode_config_kv(m)
    assert buf == bytes([0x80, 0xFE, 0xFF, 0xFF, 0xFF])
    assert decode_config_kv(buf) == m


def test_config_ack_bytes_and_round_trip():
    m = ConfigAck(0x40, ConfigResult.READ_ONLY, 250)
    buf = encode_config_ack(m)
    assert buf == bytes([0x40, 2, 0xFA, 0, 0, 0])
    assert decode_config_ack(buf) == m


def test_event_empty_and_full_text():
    buf = encode_event(Event(EventCode.SETPOINT_STALE))
    assert buf == bytes([0x01, 0x01])
    assert decode_event(buf) == Event(0x0101, b"")

    m = Event(EventCode.SETPOINT_STALE, b"x" * EVENT_TEXT_MAX)
    buf = encode_event(m)
    assert len(buf) == EVENT_MAX_SIZE
    assert decode_event(buf) == m


def test_event_too_long_refused():
    with pytest.raises(ValueError):
        encode_event(Event(0, b"x" * (EVENT_TEXT_MAX + 1)))
    assert decode_event(bytes(EVENT_MAX_SIZE + 1)) is None
    assert decode_event(bytes(EVENT_MIN_SIZE - 1)) is None


def test_state_bytes_and_round_trip():
    m = State(
        EspState.STREAMING, 1, 0x01020304, 0x05060708,
        -1, 900, 359, 1110, -300, 300, 200, 0, 7, 0xBEEF,
    )  # fmt: skip
    want = bytes([
        3, 1, 0x04, 0x03, 0x02, 0x01, 0x08, 0x07, 0x06, 0x05,
        0xFF, 0xFF, 0x84, 0x03, 0x67, 0x01, 0x56, 0x04, 0xD4, 0xFE,
        0x2C, 0x01, 200, 0, 7, 0, 0xEF, 0xBE,
    ])  # fmt: skip
    assert encode_state(m) == want
    assert decode_state(want) == m


def test_wrong_nonce_or_fw_version_size_refused():
    with pytest.raises(ValueError):
        encode_hello(Hello(1, bytes(NONCE_SIZE - 1)))
    with pytest.raises(ValueError):
        encode_hello_ack(HelloAck(1, bytes(NONCE_SIZE), b"v0.1"))


@pytest.mark.parametrize("d", [-1, 1])
def test_wrong_sizes_refused(d):
    # Every fixed-size decoder refuses one byte short and one byte long.
    assert decode_hello(bytes(HELLO_SIZE + d)) is None
    assert decode_hello_ack(bytes(HELLO_ACK_SIZE + d)) is None
    assert decode_u8_payload(bytes(1 + d)) is None
    assert decode_config_kv(bytes(CONFIG_KV_SIZE + d)) is None
    assert decode_config_ack(bytes(CONFIG_ACK_SIZE + d)) is None
    assert decode_state(bytes(STATE_SIZE + d)) is None
