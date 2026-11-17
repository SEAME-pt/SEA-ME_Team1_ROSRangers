"""ThoneFlow-3901U frame parser (#107, ThoneFlow.md §3.3 PAR-01..09, §8).

Portable: bytes in, measurements out. No UART, no board, no clock of its own
(PAR-05): the caller passes the receive time in seconds from a clock it
controls.

Frame (9 bytes), as measured on the bench on 2026-11-10 and 2026-11-16:

    0     1     2..3        4..5        6         7        8
    0xFE  0x04  X int16 LE  Y int16 LE  checksum  quality  0xAA

- X/Y are signed 16-bit, LOW byte first (the datasheet says high byte first;
  the bench shows the opposite).
- checksum = (b2 + b3 + b4 + b5) & 0xFF (0 mismatches in >40,000 frames).
- ~95.4 frames/s (the datasheet says 66).
- With no usable image (lens covered) the sensor sends X = Y = 0 with
  quality < 20, so zero motion and "no data" look the same in X/Y. Only the
  quality field tells them apart (PAR-08).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

FRAME_SIZE = 9
HEADER = 0xFE
LENGTH = 0x04
FOOTER = 0xAA


class Status(Enum):
    """ThoneFlow.md §8.1. INVALID_FRAME is never emitted: a bad frame is
    dropped and counted (§8.2)."""

    OK = "ok"
    LOW_QUALITY = "low_quality"
    STALE = "stale"
    NO_DATA = "no_data"


@dataclass(frozen=True)
class Measurement:
    """One valid frame (PAR-06)."""

    x: int  # raw counts, signed, no scaling (PAR-09)
    y: int
    quality: int  # 0..255
    status: Status  # OK or LOW_QUALITY
    t: float  # receive time, caller's clock (s)
    seq: int  # local counter of valid frames, from 1


@dataclass
class Counters:
    """PAR-07 / VAL-03."""

    accepted: int = 0
    bad_length: int = 0
    bad_footer: int = 0
    bad_checksum: int = 0
    skipped_bytes: int = 0  # bytes dropped while looking for a frame
    resyncs: int = 0  # times the parser lost alignment and found it again


def checksum(frame: bytes) -> int:
    return (frame[2] + frame[3] + frame[4] + frame[5]) & 0xFF


def decode_frame(frame: bytes) -> tuple[int, int, int] | None:
    """Returns (x, y, quality) for one complete, valid 9-byte frame, else None."""
    if len(frame) != FRAME_SIZE or frame[0] != HEADER or frame[1] != LENGTH:
        return None
    if frame[8] != FOOTER or frame[6] != checksum(frame):
        return None
    x = int.from_bytes(frame[2:4], "little", signed=True)
    y = int.from_bytes(frame[4:6], "little", signed=True)
    return x, y, frame[7]


class ThoneflowParser:
    """Byte-stream parser with status logic.

    quality_min: frames below it are LOW_QUALITY. There is no safe default:
        on 2026-11-16 the carpet gave correct motion with quality 125-180,
        while a black EVA mat gave wrong motion with quality up to ~135. Pick
        it per surface and height, and do not trust it alone.
    stale_s: with no valid frame for longer than this, status() is STALE
        (frames come every ~10.5 ms).
    """

    def __init__(self, quality_min: int, stale_s: float) -> None:
        if not 0 <= quality_min <= 255:
            raise ValueError(f"quality_min must be 0..255, got {quality_min}")
        if stale_s <= 0:
            raise ValueError(f"stale_s must be > 0, got {stale_s}")
        self.quality_min = quality_min
        self.stale_s = stale_s
        self.counters = Counters()
        self._buf = bytearray()
        self._seq = 0
        self._last: Measurement | None = None
        self._last_byte_t: float | None = None
        self._aligned = True  # becomes False after a dropped byte

    def feed(self, data: bytes, t: float) -> list[Measurement]:
        """Takes any number of bytes received at time t (PAR-01) and returns
        the valid frames completed by them, in order."""
        if data:
            self._last_byte_t = t
        self._buf += data
        out: list[Measurement] = []
        while True:
            start = self._buf.find(HEADER)
            if start < 0:
                self._drop(len(self._buf))
                break
            if start:
                self._drop(start)
            if len(self._buf) < FRAME_SIZE:
                break
            frame = bytes(self._buf[:FRAME_SIZE])
            if not self._check(frame):
                # Not a frame at this 0xFE: move on one byte only, so a real
                # frame starting inside this one is still found (PAR-03/04).
                self._drop(1)
                continue
            del self._buf[:FRAME_SIZE]
            if not self._aligned:
                self.counters.resyncs += 1
                self._aligned = True
            out.append(self._emit(frame, t))
        return out

    def status(self, now: float) -> Status:
        """§8.1. Never zero motion for "no data" (PAR-08, VAL-01)."""
        if self._last_byte_t is None or now - self._last_byte_t > self.stale_s:
            return Status.NO_DATA
        if self._last is None or now - self._last.t > self.stale_s:
            return Status.STALE
        return self._last.status

    def latest(self, now: float) -> Measurement | None:
        """The last frame, only while it is fresh (VAL-02)."""
        if self.status(now) in (Status.OK, Status.LOW_QUALITY):
            return self._last
        return None

    def reset(self) -> None:
        """Reader restart (§8.2): no carried-over values. Counters are kept."""
        self._buf.clear()
        self._last = None
        self._last_byte_t = None
        self._aligned = True

    def _check(self, frame: bytes) -> bool:
        # Reasons are counted only where a frame was expected (aligned). While
        # hunting after a loss, a 0xFE inside the data is not a bad frame, so
        # it only shows in skipped_bytes.
        reason = None
        if frame[1] != LENGTH:
            reason = "bad_length"
        elif frame[8] != FOOTER:
            reason = "bad_footer"
        elif frame[6] != checksum(frame):
            reason = "bad_checksum"
        if reason is None:
            return True
        if self._aligned:
            setattr(self.counters, reason, getattr(self.counters, reason) + 1)
        return False

    def _drop(self, n: int) -> None:
        if n:
            del self._buf[:n]
            self.counters.skipped_bytes += n
            self._aligned = False

    def _emit(self, frame: bytes, t: float) -> Measurement:
        x, y, q = decode_frame(frame)  # type: ignore[misc]  # checked above
        self._seq += 1
        self.counters.accepted += 1
        st = Status.OK if q >= self.quality_min else Status.LOW_QUALITY
        self._last = Measurement(x, y, q, st, t, self._seq)
        return self._last
