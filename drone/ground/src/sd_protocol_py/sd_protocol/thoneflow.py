"""ThoneFlow-3901U frame parser (#107, ThoneFlow.md §3.3 PAR-01..09, §8).

Portable: bytes in, measurements out. No UART, no board, no clock of its own
(PAR-05): the caller passes the receive time in seconds from a clock it
controls.

Frame (9 bytes), as measured on the bench on 2026-11-10 and 2026-11-16:

    0     1     2..3        4..5        6         7        8
    0xFE  0x04  X int16 LE  Y int16 LE  checksum  quality  0xAA

- X/Y are signed 16-bit, LOW byte first (the datasheet says high byte first;
  the bench shows the opposite).
- checksum = (b2 + b3 + b4 + b5) & 0xFF (0 mismatches in 66,297 frames). It
  does not cover the quality byte, and it is a plain sum: it catches most
  line errors, not all.
- ~95.4 frames/s (the datasheet says 66).
- With no usable image (lens covered) the sensor sends X = Y = 0 with
  quality 5-17, so zero motion and "no data" look the same in X/Y. Only the
  quality field tells them apart (PAR-08), hence QUALITY_MIN_FLOOR.

For a consumer:
- t is the time the caller read the bytes, not the time the sensor made the
  frame. All frames completed by one feed() share it, so do not take dt
  between frames from t: count frames (~10.5 ms each) or use timestamps
  taken by the reader per frame. Choose stale_s above the read period plus
  its jitter. t and now must come from one monotonic clock.
- X/Y are per-frame deltas: to integrate, sum every Measurement feed()
  returns (seq tells repeats apart). latest() is for status and quality,
  and it may return a LOW_QUALITY frame with X = Y = 0: check .status.
- A frame lost whole on the line leaves no trace in the counters; only the
  timing shows it.
- Quality is not a safety check: on a black EVA mat the motion was reversed
  with quality ~130 (ThoneFlow.md §2.4).

Not exported from sd_protocol/__init__.py: import it as sd_protocol.thoneflow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

FRAME_SIZE = 9
HEADER = 0xFE
LENGTH = 0x04
FOOTER = 0xAA

# Below 25 the sensor sends X = Y = 0 in 2,749 of 2,789 bench frames (the lens
# covered gave 5-17), so such a frame is "no image", not "not moving". A lower
# quality_min would let it out as OK (PAR-08, VAL-01), so it is refused.
QUALITY_MIN_FLOOR = 25


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
    seq: int  # valid frames of this parser object, from 1; reset() keeps it


@dataclass
class Counters:
    """PAR-07 / VAL-03. The reject counters count only where a frame was
    expected (the parser was aligned); bytes thrown away while hunting for
    the next frame show in skipped_bytes."""

    bytes_in: int = 0
    accepted: int = 0
    bad_header: int = 0  # a frame was expected, the byte there was not 0xFE
    bad_length: int = 0
    bad_footer: int = 0
    bad_checksum: int = 0
    skipped_bytes: int = 0  # bytes dropped while looking for a frame
    resyncs: int = 0  # frames found again after a loss (not the first sync)
    stale_events: int = 0  # times the data stopped being fresh (STALE/NO_DATA)


def checksum(frame: bytes) -> int:
    return (frame[2] + frame[3] + frame[4] + frame[5]) & 0xFF


def _reject_reason(frame: bytes) -> str | None:
    """Why a 9-byte block that starts with 0xFE is not a frame, or None."""
    if frame[1] != LENGTH:
        return "bad_length"
    if frame[8] != FOOTER:
        return "bad_footer"
    if frame[6] != checksum(frame):
        return "bad_checksum"
    return None


def _fields(frame: bytes) -> tuple[int, int, int]:
    x = int.from_bytes(frame[2:4], "little", signed=True)
    y = int.from_bytes(frame[4:6], "little", signed=True)
    return x, y, frame[7]


def decode_thoneflow_frame(frame: bytes) -> tuple[int, int, int] | None:
    """Returns (x, y, quality) for one complete, valid 9-byte frame, else None."""
    if len(frame) != FRAME_SIZE or frame[0] != HEADER or _reject_reason(frame):
        return None
    return _fields(frame)


class ThoneflowParser:
    """Byte-stream parser with status logic.

    quality_min: frames below it are LOW_QUALITY. At least QUALITY_MIN_FLOOR
        (25). There is no safe default: on 2026-11-16 the carpet gave correct
        motion with quality 125-180, while a black EVA mat gave wrong motion
        with quality up to ~135. Pick it per surface and height, and do not
        trust it alone.
    stale_s: with no valid frame for longer than this, status() is STALE.
        Frames come every ~10.5 ms, but t is the read time: see the module
        notes.
    """

    def __init__(self, quality_min: int, stale_s: float) -> None:
        if not QUALITY_MIN_FLOOR <= quality_min <= 255:
            raise ValueError(
                f"quality_min must be {QUALITY_MIN_FLOOR}..255, got {quality_min}"
            )
        if not (math.isfinite(stale_s) and stale_s > 0):
            raise ValueError(f"stale_s must be finite and > 0, got {stale_s}")
        self.quality_min = quality_min
        self.stale_s = stale_s
        self.counters = Counters()
        self._buf = bytearray()
        self._seq = 0
        self._last: Measurement | None = None
        self._last_byte_t: float | None = None
        # The reader may open mid-frame, so the first 0xFE is not trusted to
        # be a frame start until one validates (no reject counted before).
        self._aligned = False
        self._synced = False  # a frame was found since start or reset()
        self._stale_counted = False

    def feed(self, data: bytes, t: float) -> list[Measurement]:
        """Takes any number of bytes received at time t (PAR-01) and returns
        the valid frames completed by them, in order."""
        if data:
            self._last_byte_t = t
            self.counters.bytes_in += len(data)
        self._buf += data
        buf = self._buf
        out: list[Measurement] = []
        i = 0
        while i < len(buf):
            if buf[i] != HEADER:
                # Counted here, at the first byte where a frame was expected,
                # so the count does not depend on how the reads split the
                # stream.
                if self._aligned:
                    self.counters.bad_header += 1
                start = buf.find(HEADER, i)
                end = len(buf) if start < 0 else start
                self._skip(end - i)
                i = end
                continue
            if len(buf) - i < FRAME_SIZE:
                break
            frame = bytes(buf[i : i + FRAME_SIZE])
            reason = _reject_reason(frame)
            if reason:
                if self._aligned:
                    setattr(self.counters, reason, getattr(self.counters, reason) + 1)
                # Not a frame at this 0xFE: move on one byte only, so a real
                # frame starting inside this one is still found (PAR-03/04).
                self._skip(1)
                i += 1
                continue
            i += FRAME_SIZE
            if not self._aligned:
                if self._synced:
                    self.counters.resyncs += 1
                self._aligned = self._synced = True
            out.append(self._emit(frame, t))
        del buf[:i]  # trimmed once per feed, not byte by byte
        return out

    def status(self, now: float) -> Status:
        """§8.1. Never zero motion for "no data" (PAR-08, VAL-01)."""
        if self._last_byte_t is None or not self._fresh(self._last_byte_t, now):
            st = Status.NO_DATA
        elif self._last is None or not self._fresh(self._last.t, now):
            st = Status.STALE
        else:
            return self._last.status
        if self._last is not None:
            self._count_stale()
        return st

    def latest(self, now: float) -> Measurement | None:
        """The last frame, only while it is fresh (VAL-02). Not for
        integrating motion (sum what feed() returns), and it can be a
        LOW_QUALITY frame with X = Y = 0: check .status."""
        if self.status(now) in (Status.OK, Status.LOW_QUALITY):
            return self._last
        return None

    def reset(self) -> None:
        """Reader restart (§8.2): no carried-over values. Counters and seq
        go on, so a consumer never sees a seq number twice."""
        self._buf.clear()
        self._last = None
        self._last_byte_t = None
        self._aligned = False
        self._synced = False
        self._stale_counted = False

    def _fresh(self, t0: float, now: float) -> bool:
        # A clock that went back (now < t0) proves nothing about freshness.
        return 0 <= now - t0 <= self.stale_s

    def _count_stale(self) -> None:
        # One event per loss of fresh data, whether status() sees it or the
        # next frame shows the gap.
        if not self._stale_counted:
            self.counters.stale_events += 1
            self._stale_counted = True

    def _skip(self, n: int) -> None:
        self.counters.skipped_bytes += n
        self._aligned = False

    def _emit(self, frame: bytes, t: float) -> Measurement:
        if self._last is not None and not self._fresh(self._last.t, t):
            self._count_stale()
        self._stale_counted = False
        x, y, q = _fields(frame)
        self._seq += 1
        self.counters.accepted += 1
        st = Status.OK if q >= self.quality_min else Status.LOW_QUALITY
        self._last = Measurement(x, y, q, st, t, self._seq)
        return self._last
