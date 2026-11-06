"""Frame encode/decode: 20-byte header + payload + 8-byte tag (spec §1, §4 rule 1).

All integers are little-endian. Session and sequence checks are the caller's
(spec §4 rules 2 and 3).
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

from sd_protocol.crypto import KEY_SIZE, equal_constant_time, hmac_sha256
from sd_protocol.version import protocol_version

MAGIC = b"SD"
WIRE_VERSION = protocol_version()
HEADER_SIZE = 20
TAG_SIZE = 8
MAX_PAYLOAD = 256
MAX_FRAME = HEADER_SIZE + MAX_PAYLOAD + TAG_SIZE

# magic, version, type, flags, reserved, payload_len, session_id, seq, t_send_us
_HEADER = struct.Struct("<2sBBBBHIII")


class MsgType(IntEnum):
    """Spec §3. Values are part of the wire format: never renumber."""

    HELLO = 0x01
    HELLO_ACK = 0x02
    ARM_CONTROL_REQUEST = 0x03
    ARM_CONTROL_ACK = 0x04
    RESUME_REQUEST = 0x05
    RESUME_ACK = 0x06
    BYE = 0x07
    CONFIG_GET = 0x20
    CONFIG_SET = 0x21
    CONFIG_ACK = 0x22  # reply to CONFIG_GET / CONFIG_SET (spec v1.1, #142)
    EVENT = 0x40
    SETPOINT = 0x60
    STATE = 0x80


class RxError(IntEnum):
    """Why a frame was rejected. Every reject is dropped and counted (REQ-COM-004)."""

    OK = 0
    TRUNCATED = 1  # shorter than header + tag, or than payload_len says
    BAD_MAGIC = 2  # not "SD"
    BAD_VERSION = 3  # version byte is not WIRE_VERSION
    BAD_LENGTH = 4  # payload_len > MAX_PAYLOAD, or bytes after the frame
    BAD_TAG = 5  # HMAC tag does not match


@dataclass(frozen=True)
class Header:
    type: int
    flags: int = 0
    session_id: int = 0
    seq: int = 0
    t_send_us: int = 0


@dataclass(frozen=True)
class Frame:
    header: Header
    payload: bytes


def _tag(data: bytes, key: bytes) -> bytes:
    return hmac_sha256(key, data)[:TAG_SIZE]


def encode_frame(header: Header, payload: bytes, key: bytes) -> bytes:
    """Builds header + payload + tag. payload_len is taken from `payload`."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload is {len(payload)} bytes, the limit is {MAX_PAYLOAD}")
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes")
    head = _HEADER.pack(
        MAGIC,
        WIRE_VERSION,
        header.type,
        header.flags,
        0,  # reserved
        len(payload),
        header.session_id,
        header.seq,
        header.t_send_us,
    )
    body = head + bytes(payload)
    return body + _tag(body, key)


def decode_frame(buf: bytes, key: bytes) -> tuple[RxError, Frame | None]:
    """Checks one complete frame (a UDP datagram, or a TCP frame cut with frame_size()).

    Order of checks follows spec §4 rule 1: length, magic, version, then the tag.
    Returns (RxError.OK, frame) or (the reason, None).
    """
    if len(buf) < HEADER_SIZE + TAG_SIZE:
        return RxError.TRUNCATED, None
    magic, version, msg_type, flags, _, payload_len, session_id, seq, t_send_us = (
        _HEADER.unpack_from(buf)
    )
    if magic != MAGIC:
        return RxError.BAD_MAGIC, None
    if version != WIRE_VERSION:
        return RxError.BAD_VERSION, None
    if payload_len > MAX_PAYLOAD:
        return RxError.BAD_LENGTH, None
    total = HEADER_SIZE + payload_len + TAG_SIZE
    if len(buf) < total:
        return RxError.TRUNCATED, None
    if len(buf) > total:
        return RxError.BAD_LENGTH, None
    body = bytes(buf[: HEADER_SIZE + payload_len])
    if not equal_constant_time(
        _tag(body, key), bytes(buf[HEADER_SIZE + payload_len :])
    ):
        return RxError.BAD_TAG, None
    header = Header(msg_type, flags, session_id, seq, t_send_us)
    return RxError.OK, Frame(header, body[HEADER_SIZE:])


def frame_size(buf: bytes) -> int:
    """TCP framing (OD-03): the size of the whole frame from its first HEADER_SIZE bytes.

    Returns 0 if fewer than HEADER_SIZE bytes are given or payload_len > MAX_PAYLOAD.
    """
    if len(buf) < HEADER_SIZE:
        return 0
    payload_len = struct.unpack_from("<H", buf, 6)[0]
    if payload_len > MAX_PAYLOAD:
        return 0
    return HEADER_SIZE + payload_len + TAG_SIZE
