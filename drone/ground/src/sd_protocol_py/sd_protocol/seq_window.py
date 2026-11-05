"""Sequence window (ADR-003): accept a message only if its seq is newer than the last one."""

from dataclasses import dataclass
from enum import IntEnum

MAX_SEQ = 0x80000000  # 2^31: from here the sender must open a new session


class SeqResult(IntEnum):
    ACCEPTED = 0
    ACCEPTED_GAP = 1  # newer, but some seq numbers were skipped (counted, not an error)
    DUPLICATE = 2  # same seq as the last accepted one
    OLD = 3  # older than the last accepted one (reordered or replayed), or seq 0
    NEED_NEW_SESSION = 4  # seq >= 2^31: the sender must open a new session


@dataclass
class SeqCounters:
    duplicates: int = 0
    old: int = 0
    gaps: int = 0  # number of accepted messages that came after a gap
    missing: int = 0  # total seq numbers skipped over all gaps
    too_high: int = 0


class SeqWindow:
    """One window per sender, per session and per transport (spec §1, ADR-003).

    Reset it when a new session starts.
    """

    def __init__(self) -> None:
        self._last = 0
        self.counters = SeqCounters()

    @property
    def last(self) -> int:
        return self._last

    def accept(self, seq: int) -> SeqResult:
        if seq >= MAX_SEQ:
            self.counters.too_high += 1
            return SeqResult.NEED_NEW_SESSION
        if seq == self._last and seq != 0:
            self.counters.duplicates += 1
            return SeqResult.DUPLICATE
        if seq <= self._last:
            self.counters.old += 1
            return SeqResult.OLD
        skipped = seq - self._last - 1
        self._last = seq
        if skipped > 0:
            self.counters.gaps += 1
            self.counters.missing += skipped
            return SeqResult.ACCEPTED_GAP
        return SeqResult.ACCEPTED

    def reset(self) -> None:
        self._last = 0
        self.counters = SeqCounters()
