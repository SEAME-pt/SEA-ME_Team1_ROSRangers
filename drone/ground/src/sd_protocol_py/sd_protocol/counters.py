"""Ground-side totals of the ESP's wrapping u16 counters (spec §7.5, #149)."""


class WrapCounter:
    """Total of a u16 counter that wraps to 0 after 65535, such as
    STATE.rx_rejected.

    Feed it with update(). When a value is lower than the previous one the
    counter wrapped, so 65536 is added. Wraps before the first value are not
    known: the total starts at the first value received.

    What the caller must do:
      * Feed only values from STATEs a Receiver accepted, in the order they
        were accepted. The Receiver accepts only a seq newer than the last
        one (ADR-003), so a replayed or reordered STATE, with its older and
        lower value, never reaches update() and can't fake a wrap.
      * A wrap is missed if the counter moves by 65536 or more between two
        values fed. If the values can be far apart (no STATE for a while),
        call reset() first after a long gap: the total then starts again from
        the next value instead of reading 65536 too low.

    Call reset() on every new session (each HELLO_ACK). STATE carries no boot
    id, so the ground cannot tell an ESP reboot from a reconnect; without the
    reset, a reboot (e.g. 500 -> 3) would look like a wrap, a false jump of
    65,536 rejects. A reboot always ends the session, so this covers it; the
    wraps of an earlier session on the same boot are forgotten (the total is
    for diagnostics only).
    """

    MODULUS = 0x10000

    def __init__(self) -> None:
        self._last: int | None = None
        self._wraps = 0

    def update(self, raw: int) -> int:
        """Takes one received value (0..65535) and returns the total."""
        if not 0 <= raw < self.MODULUS:
            raise ValueError(f"not a u16: {raw}")
        if self._last is not None and raw < self._last:
            self._wraps += 1
        self._last = raw
        return self.total

    @property
    def wraps(self) -> int:
        return self._wraps

    @property
    def total(self) -> int:
        """Wraps × 65536 + the last value; 0 before the first update()."""
        return self._wraps * self.MODULUS + (self._last or 0)

    def reset(self) -> None:
        """Forget everything. Call it on every new session (HELLO_ACK)."""
        self._last = None
        self._wraps = 0
