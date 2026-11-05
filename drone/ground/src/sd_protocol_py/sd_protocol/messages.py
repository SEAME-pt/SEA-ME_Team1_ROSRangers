"""Payload codecs for the spec §3 messages other than SETPOINT (setpoint.py).

Python twin of lib/protocol messages.hpp. Same contract as encode_setpoint and
decode_setpoint: decode checks the size only (None for a wrong size), values are
not range-checked. Encode raises ValueError for fields that do not fit.
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

# --- Code values: Protocol Spec v0 §3.1 (v1.1, decided on #142, 2026-11-05).
# Change here and in messages.hpp together. Values are part of the wire format:
# never renumber or reuse.


class AckResult(IntEnum):
    """ARM_CONTROL_ACK.result and RESUME_ACK.result."""

    OK = 0
    WRONG_STATE = 1
    MASK_MISMATCH = 2
    RESUME_CONDITION_NOT_MET = 3


class ByeReason(IntEnum):
    NORMAL = 0
    TIMEOUT = 1
    PROTOCOL_ERROR = 2
    REPLACED = 3  # replaced by a new session


class ConfigResult(IntEnum):
    """CONFIG_ACK.result."""

    OK = 0
    UNKNOWN_KEY = 1
    READ_ONLY = 2  # CONFIG_SET on an Info key (0x01-0x3F)
    SAFETY_PARAMETER = 3  # CONFIG_SET on a Safety key (0x40-0x7F): firmware only
    OUT_OF_RANGE = 4  # never clamped (spec §7.3)
    CONTROL_ENABLED = 5  # any CONFIG_SET while STREAMING (OD-16)


class EspState(IntEnum):
    """STATE.esp_state. A stale setpoint is an event, not a state."""

    BOOT = 0
    NO_SESSION = 1
    SESSION_IDLE = 2
    STREAMING = 3
    LINK_FAILSAFE = 4


class EventCode(IntEnum):
    """EVENT.code, grouped by range: 0x00xx system, 0x01xx link, 0x02xx FC / MSP."""

    SETPOINT_STALE = 0x0101
    LINK_FAILSAFE_ENTERED = 0x0102
    LINK_RESUMED = 0x0103


class Transport(IntEnum):
    """Spec §2: 0x01-0x5F travel on TCP, 0x60-0x9F on UDP."""

    UDP = 0
    TCP = 1


def transport_of(msg_type: int) -> Transport | None:
    """The transport a type belongs to (spec §2). None outside both ranges."""
    if 0x01 <= msg_type <= 0x5F:
        return Transport.TCP
    if 0x60 <= msg_type <= 0x9F:
        return Transport.UDP
    return None


# --- Payloads.

NONCE_SIZE = 16
FW_VERSION_SIZE = 12
EVENT_TEXT_MAX = 64

HELLO_SIZE = 1 + NONCE_SIZE
HELLO_ACK_SIZE = 4 + NONCE_SIZE + FW_VERSION_SIZE
CONFIG_KV_SIZE = 5
CONFIG_ACK_SIZE = 6
EVENT_MIN_SIZE = 2
EVENT_MAX_SIZE = 2 + EVENT_TEXT_MAX
STATE_SIZE = 28

_HELLO = struct.Struct(f"<B{NONCE_SIZE}s")
_HELLO_ACK = struct.Struct(f"<I{NONCE_SIZE}s{FW_VERSION_SIZE}s")
_CONFIG_KV = struct.Struct("<Bi")
_CONFIG_ACK = struct.Struct("<BBi")
_EVENT_CODE = struct.Struct("<H")
# esp_state, override_active, last_ctrl_seq, last_ctrl_t_send_us, roll, pitch,
# heading, vbat, flow_x, flow_y, flow_quality, flow_status, rx_rejected, msp_errors
_STATE = struct.Struct("<BBIIhhHHhhBBHH")


def _check_size(name: str, value: bytes, size: int) -> None:
    if len(value) != size:
        raise ValueError(f"{name} must be {size} bytes, got {len(value)}")


@dataclass(frozen=True)
class Hello:
    """HELLO (0x01)."""

    proto: int
    ground_nonce: bytes  # NONCE_SIZE bytes


@dataclass(frozen=True)
class HelloAck:
    """HELLO_ACK (0x02)."""

    session_id: int
    esp_nonce: bytes  # NONCE_SIZE bytes
    fw_version: bytes  # FW_VERSION_SIZE raw bytes, not NUL-terminated


@dataclass(frozen=True)
class ConfigKv:
    """CONFIG_GET (0x20) and CONFIG_SET (0x21): same payload."""

    key: int
    value: int


@dataclass(frozen=True)
class ConfigAck:
    """CONFIG_ACK (0x22), reply to CONFIG_GET and CONFIG_SET."""

    key: int
    result: int  # ConfigResult
    value: int


@dataclass(frozen=True)
class Event:
    """EVENT (0x40). The text is payload_len - 2 bytes, up to EVENT_TEXT_MAX."""

    code: int  # EventCode
    text: bytes = b""  # not NUL-terminated


@dataclass(frozen=True)
class State:
    """STATE (0x80)."""

    esp_state: int  # EspState
    override_active: int
    last_ctrl_seq: int
    last_ctrl_t_send_us: int
    roll: int  # 0.1 deg
    pitch: int  # 0.1 deg
    heading: int  # deg
    vbat: int  # 0.01 V
    flow_x: int
    flow_y: int
    flow_quality: int
    flow_status: int  # ThoneFlow §8.1
    rx_rejected: int
    msp_errors: int


def encode_hello(m: Hello) -> bytes:
    _check_size("ground_nonce", m.ground_nonce, NONCE_SIZE)
    return _HELLO.pack(m.proto, m.ground_nonce)


def decode_hello(payload: bytes) -> Hello | None:
    if len(payload) != HELLO_SIZE:
        return None
    return Hello(*_HELLO.unpack(payload))


def encode_hello_ack(m: HelloAck) -> bytes:
    _check_size("esp_nonce", m.esp_nonce, NONCE_SIZE)
    _check_size("fw_version", m.fw_version, FW_VERSION_SIZE)
    return _HELLO_ACK.pack(m.session_id, m.esp_nonce, m.fw_version)


def decode_hello_ack(payload: bytes) -> HelloAck | None:
    if len(payload) != HELLO_ACK_SIZE:
        return None
    return HelloAck(*_HELLO_ACK.unpack(payload))


def encode_u8_payload(v: int) -> bytes:
    """One-byte payloads: ARM_CONTROL_REQUEST.mask, ARM_CONTROL_ACK.result,
    RESUME_ACK.result, BYE.reason. RESUME_REQUEST has an empty payload.
    """
    return bytes([v])


def decode_u8_payload(payload: bytes) -> int | None:
    if len(payload) != 1:
        return None
    return payload[0]


def encode_config_kv(m: ConfigKv) -> bytes:
    return _CONFIG_KV.pack(m.key, m.value)


def decode_config_kv(payload: bytes) -> ConfigKv | None:
    if len(payload) != CONFIG_KV_SIZE:
        return None
    return ConfigKv(*_CONFIG_KV.unpack(payload))


def encode_config_ack(m: ConfigAck) -> bytes:
    return _CONFIG_ACK.pack(m.key, m.result, m.value)


def decode_config_ack(payload: bytes) -> ConfigAck | None:
    if len(payload) != CONFIG_ACK_SIZE:
        return None
    return ConfigAck(*_CONFIG_ACK.unpack(payload))


def encode_event(m: Event) -> bytes:
    if len(m.text) > EVENT_TEXT_MAX:
        raise ValueError(
            f"event text is {len(m.text)} bytes, the limit is {EVENT_TEXT_MAX}"
        )
    return _EVENT_CODE.pack(m.code) + bytes(m.text)


def decode_event(payload: bytes) -> Event | None:
    if not EVENT_MIN_SIZE <= len(payload) <= EVENT_MAX_SIZE:
        return None
    (code,) = _EVENT_CODE.unpack_from(payload)
    return Event(code, bytes(payload[EVENT_MIN_SIZE:]))


def encode_state(m: State) -> bytes:
    return _STATE.pack(
        m.esp_state,
        m.override_active,
        m.last_ctrl_seq,
        m.last_ctrl_t_send_us,
        m.roll,
        m.pitch,
        m.heading,
        m.vbat,
        m.flow_x,
        m.flow_y,
        m.flow_quality,
        m.flow_status,
        m.rx_rejected,
        m.msp_errors,
    )


def decode_state(payload: bytes) -> State | None:
    if len(payload) != STATE_SIZE:
        return None
    return State(*_STATE.unpack(payload))
