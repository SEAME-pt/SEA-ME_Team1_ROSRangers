"""SETPOINT (0x60) payload and its mapping to RC channels (spec §3, §4 rule 4, §5)."""

import struct
from dataclasses import dataclass

SETPOINT_SIZE = 10
RC_CHANNELS = 8
RC_MIN = 1000
RC_MID = 1500
RC_MAX = 2000

# mode, roll, pitch, yaw_rate, throttle, flags
_SETPOINT = struct.Struct("<BhhhhB")


@dataclass(frozen=True)
class Setpoint:
    mode: int  # 0 neutral, 1 attitude
    roll: int  # -1000..1000
    pitch: int  # -1000..1000
    yaw_rate: int  # -1000..1000
    throttle: int  # 0..1000
    flags: int = 0  # reserved in v0 (spec §7.5)


def encode_setpoint(sp: Setpoint) -> bytes:
    """Returns the SETPOINT_SIZE-byte payload. Values are sent as given, not clamped."""
    return _SETPOINT.pack(
        sp.mode, sp.roll, sp.pitch, sp.yaw_rate, sp.throttle, sp.flags
    )


def decode_setpoint(payload: bytes) -> Setpoint | None:
    """Reads a payload of exactly SETPOINT_SIZE bytes; None for any other size.

    Values are not range-checked here.
    """
    if len(payload) != SETPOINT_SIZE:
        return None
    return Setpoint(*_SETPOINT.unpack(payload))


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _axis_to_us(v: int) -> int:
    # -1000..1000 -> 1000..2000 us. int(v / 2) rounds toward the centre, like the
    # integer division of the C++ (v // 2 would round -1 down to 1499).
    return RC_MID + int(_clamp(v, -1000, 1000) / 2)


def setpoint_to_rc(sp: Setpoint) -> list[int]:
    """Clamps and maps to MSP_SET_RAW_RC channels in AETR order.

    [roll, pitch, throttle, yaw, aux1..aux4]: -1000..1000 -> 1000..2000 us,
    throttle 0..1000 -> 1000..2000 us, aux always 1500 (never driven by the
    ground). `mode` is not used here: deciding what a neutral or stale setpoint
    sends is the link state machine's job (#102).
    """
    return [
        _axis_to_us(sp.roll),
        _axis_to_us(sp.pitch),
        RC_MIN + _clamp(sp.throttle, 0, 1000),
        _axis_to_us(sp.yaw_rate),
    ] + [RC_MID] * (RC_CHANNELS - 4)
