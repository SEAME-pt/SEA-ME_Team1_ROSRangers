"""Minimal MSP v1/v2 encoder and decoder for the SITL tests.

sd_protocol has no MSP code yet. This moves into sd_protocol once it does
(issue #85 notes).

v1: '$' 'M' dir size cmd payload xor(size, cmd, payload)
v2: '$' 'X' dir flag cmd16 size16 payload crc8_dvb_s2(flag..payload)
dir is '<' to the FC, '>' from the FC, '!' for an error reply.
"""

import struct
from dataclasses import dataclass

MSP_API_VERSION = 1

TO_FC = b"<"
FROM_FC = b">"
ERROR = b"!"


def crc8_dvb_s2(data: bytes, crc: int = 0) -> int:
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def xor_checksum(data: bytes) -> int:
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum


def encode_v1(cmd: int, payload: bytes = b"", direction: bytes = TO_FC) -> bytes:
    body = bytes([len(payload), cmd]) + payload
    return b"$M" + direction + body + bytes([xor_checksum(body)])


def encode_v2(
    cmd: int, payload: bytes = b"", direction: bytes = TO_FC, flag: int = 0
) -> bytes:
    body = struct.pack("<BHH", flag, cmd, len(payload)) + payload
    return b"$X" + direction + body + bytes([crc8_dvb_s2(body)])


@dataclass
class Frame:
    version: int
    direction: bytes
    cmd: int
    payload: bytes


def decode(buf: bytes) -> tuple[Frame | None, int]:
    """First complete, valid frame in buf and the number of bytes consumed.

    Returns (None, n) when no full frame is there yet: the caller drops n
    bytes (noise or a frame with a bad checksum) and keeps the rest.
    """
    start = buf.find(b"$")
    if start < 0:
        return None, len(buf)
    frame = buf[start:]
    if len(frame) < 3:
        return None, start
    kind, direction = frame[1:2], frame[2:3]
    if kind not in (b"M", b"X") or direction not in (TO_FC, FROM_FC, ERROR):
        return None, start + 1

    if kind == b"M":
        if len(frame) < 5:
            return None, start
        size, cmd = frame[3], frame[4]
        end = 5 + size + 1
        if len(frame) < end:
            return None, start
        body = frame[3 : end - 1]
        if xor_checksum(body) != frame[end - 1]:
            return None, start + end
        return Frame(1, direction, cmd, bytes(body[2:])), start + end

    if len(frame) < 8:
        return None, start
    _flag, cmd, size = struct.unpack_from("<BHH", frame, 3)
    end = 8 + size + 1
    if len(frame) < end:
        return None, start
    body = frame[3 : end - 1]
    if crc8_dvb_s2(body) != frame[end - 1]:
        return None, start + end
    return Frame(2, direction, cmd, bytes(body[5:])), start + end
