"""Ground-side totals of the ESP's wrapping u16 counters (spec §7.5, #149)."""


class WrapCounter:
    """Total of a u16 counter that wraps to 0 after 65535, such as
    STATE.rx_rejected.

    Feed every value received with update(). When a value is lower than the
    previous one the counter wrapped, so 65536 is added. A wrap is missed only
    if the counter moves by 65536 or more between two values the ground
    receives (STATE comes at 25 Hz). Wraps before the first value are not
    known: the total starts at the first value received.
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
        """Forget everything, e.g. when the ESP rebooted (new boot, new count)."""
        self._last = None
        self._wraps = 0
