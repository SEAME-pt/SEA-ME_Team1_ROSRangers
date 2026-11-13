"""Checks of the PX4 SITL smoke test (make sim-smoke, #86), with NO ROS imports.

smoke_scenario.py records what the chain did; these functions judge it, so the
pass/fail rules are unit-tested on any machine. Every check returns a Check:
name, ok, and a one-line detail with the measured values.

    REQ-SW-001   /mission/velocity_target at 20 Hz
    REQ-CTL-001  commanded speeds within the mission limits; measured speed
                 within the hard ceiling
    REQ-SAF-017  zero velocity within 0.25 s of losing /gesture/intent
    REQ-SAF-018  no axis outside the override mask commanded or moving
    direction    the drone moves the way the gesture says, and only that way
"""

import math
from collections import namedtuple

Check = namedtuple("Check", "name ok detail")

# mission_node defaults (REQ-CTL-001) and the hard ceilings of mission_node and
# the bridge. EPS absorbs float noise in the published values.
LIMIT_LATERAL_MPS = 0.3
LIMIT_VERTICAL_MPS = 0.2
LIMIT_YAW_DPS = 20.0
CEILING_LINEAR_MPS = 0.5
EPS = 1e-6

RATE_HZ = 20.0
RATE_TOLERANCE = 0.1  # 18-22 Hz
MAX_GAP_SEC = 0.15  # three ticks: one late tick is fine, a stall is not

LOSS_LIMIT_SEC = 0.25  # REQ-SAF-017, at the mission_node output
BRIDGE_TICK_SEC = 0.05  # the bridge forwards the zero at its next 20 Hz tick

# Body axes (FLU) per override mask, as in mission_node.
AXES = ("forward", "left", "up", "yaw")
MASK_AXES = {
    8: {"yaw"},
    11: {"forward", "left", "yaw"},
    15: {"forward", "left", "up", "yaw"},
}

# Movement: the commanded axis must move at least MIN_MOVE_M the right way;
# every other axis (and an axis outside the mask) at most MAX_DRIFT_M.
MIN_MOVE_M = 0.3
MAX_DRIFT_M = 0.25


def is_zero(values):
    """True when every component is exactly zero (what the nodes publish)."""
    return all(v == 0.0 for v in values)


def owned_axes(mask):
    if mask not in MASK_AXES:
        raise ValueError(f"mask must be 8, 11 or 15, got {mask!r}")
    return MASK_AXES[mask]


# ---------- REQ-SW-001 ----------
def rate_check(stamps, name="/mission/velocity_target rate"):
    """stamps: receive times (s, increasing). 20 Hz +-10 %, no gap above MAX_GAP_SEC."""
    if len(stamps) < 2:
        return Check(name, False, f"{len(stamps)} messages")
    span = stamps[-1] - stamps[0]
    hz = (len(stamps) - 1) / span if span > 0 else math.inf
    gap = max(b - a for a, b in zip(stamps, stamps[1:]))
    ok = abs(hz - RATE_HZ) <= RATE_HZ * RATE_TOLERANCE and gap <= MAX_GAP_SEC
    return Check(
        name, ok, f"{hz:.1f} Hz over {span:.1f} s, largest gap {gap * 1000:.0f} ms"
    )


# ---------- REQ-CTL-001 ----------
def limits_check(targets):
    """targets: (vx, vy, vz, wz) of /mission/velocity_target, body FLU, rad/s."""
    lateral = max((math.hypot(t[0], t[1]) for t in targets), default=0.0)
    vertical = max((abs(t[2]) for t in targets), default=0.0)
    yaw = max((math.degrees(abs(t[3])) for t in targets), default=0.0)
    ok = (
        bool(targets)
        and lateral <= LIMIT_LATERAL_MPS + EPS
        and vertical <= LIMIT_VERTICAL_MPS + EPS
        and yaw <= LIMIT_YAW_DPS + EPS
    )
    detail = (
        f"max lateral {lateral:.3f} m/s (<= {LIMIT_LATERAL_MPS}), vertical "
        f"{vertical:.3f} m/s (<= {LIMIT_VERTICAL_MPS}), yaw {yaw:.1f} deg/s "
        f"(<= {LIMIT_YAW_DPS:.0f}), {len(targets)} targets"
    )
    return Check("commanded speed (REQ-CTL-001)", ok, detail)


def measured_speed_check(velocities):
    """velocities: (v_north, v_east, v_down) PX4 measured. Within the hard ceiling."""
    horizontal = max((math.hypot(v[0], v[1]) for v in velocities), default=0.0)
    vertical = max((abs(v[2]) for v in velocities), default=0.0)
    top = CEILING_LINEAR_MPS
    ok = bool(velocities) and horizontal <= top and vertical <= top
    detail = (
        f"max horizontal {horizontal:.2f} m/s, vertical {vertical:.2f} m/s (<= {top})"
    )
    return Check("measured speed (REQ-CTL-001 ceiling)", ok, detail)


# ---------- REQ-SAF-018 ----------
def mask_targets_check(targets, mask):
    """No /mission/velocity_target with a non-zero axis outside the mask."""
    owned = owned_axes(mask)
    bad = sum(
        1
        for t in targets
        if any(v != 0.0 for axis, v in zip(AXES, t) if axis not in owned)
    )
    ok = bool(targets) and bad == 0
    return Check(
        f"mission axes outside mask {mask} (REQ-SAF-018)",
        ok,
        f"{bad} of {len(targets)} targets command an axis outside the mask",
    )


def mask_setpoints_check(setpoints, mask):
    """setpoints: (v_north, v_east, v_down, yawspeed) sent to PX4.

    Mask 11 must send v_down 0; mask 8 must send no velocity at all. With
    mask 15 every axis is owned.
    """
    owned = owned_axes(mask)

    def outside(sp):
        n, e, d, _ = sp
        horizontal = "forward" not in owned and (n != 0.0 or e != 0.0)
        vertical = "up" not in owned and d != 0.0
        return horizontal or vertical

    bad = sum(1 for sp in setpoints if outside(sp))
    ok = bool(setpoints) and bad == 0
    return Check(
        f"PX4 setpoints outside mask {mask} (REQ-SAF-018)",
        ok,
        f"{bad} of {len(setpoints)} setpoints move an axis outside the mask",
    )


# ---------- direction ----------
def body_displacement(start, end, yaw):
    """NED positions (n, e, d) and the PX4 heading (rad, from north clockwise)
    -> (forward, left, up) in metres, in the body frame at the start."""
    dn, de, dd = (b - a for a, b in zip(start, end))
    forward = dn * math.cos(yaw) + de * math.sin(yaw)
    right = -dn * math.sin(yaw) + de * math.cos(yaw)
    return forward, -right, -dd


def movement_check(name, displacement, axis, sign, mask):
    """The commanded axis moved MIN_MOVE_M the right way and nothing else drifted.

    displacement: (forward, left, up). axis: "forward", "left" or "up".
    sign: +1 or -1. An axis outside the mask must not move at all (REQ-SAF-018).
    """
    owned = owned_axes(mask)
    moves = dict(zip(AXES[:3], displacement))
    parts = [f"{a} {v:+.2f} m" for a, v in moves.items()]
    if axis in owned:
        moved = moves[axis] * sign >= MIN_MOVE_M
        others = all(abs(v) <= MAX_DRIFT_M for a, v in moves.items() if a != axis)
        rule = f"{axis} {'+' if sign > 0 else '-'}>= {MIN_MOVE_M} m, others <= {MAX_DRIFT_M} m"
        ok = moved and others
    else:
        ok = all(abs(v) <= MAX_DRIFT_M for v in moves.values())
        rule = f"{axis} not in mask {mask}: every axis <= {MAX_DRIFT_M} m"
    return Check(name, ok, f"{', '.join(parts)} ({rule})")


# ---------- REQ-SAF-017 ----------
def first_zero_after(samples, t0):
    """samples: (t, values). Time of the first all-zero sample after t0, or None."""
    for t, values in samples:
        if t > t0 and is_zero(values):
            return t
    return None


def loss_checks(last_intent, targets, setpoints):
    """Zero velocity after /gesture/intent stops.

    last_intent: receive time of the last /gesture/intent before intent_node was
    killed. targets / setpoints: (t, values) of /mission/velocity_target and
    /fmu/in/trajectory_setpoint. REQ-SAF-017 is about the mission_node output
    (<= 0.25 s). The bridge forwards it at its next tick, so PX4 may get it
    one tick (0.05 s) later.
    """
    name = "zero after losing /gesture/intent (REQ-SAF-017)"
    if last_intent is None:
        return [Check(name, False, "no /gesture/intent received")]
    if not any(t <= last_intent and not is_zero(v) for t, v in targets):
        return [
            Check(name, False, "the drone was not commanded to move before the loss")
        ]
    checks = []
    for label, samples, limit in (
        ("mission_node output", targets, LOSS_LIMIT_SEC),
        ("PX4 setpoint", setpoints, LOSS_LIMIT_SEC + BRIDGE_TICK_SEC),
    ):
        t_zero = first_zero_after(samples, last_intent)
        if t_zero is None:
            checks.append(Check(f"{name}: {label}", False, "never zero"))
            continue
        delay = t_zero - last_intent
        checks.append(
            Check(
                f"{name}: {label}",
                delay <= limit,
                f"{delay * 1000:.0f} ms (<= {limit * 1000:.0f} ms)",
            )
        )
    return checks


def summary(checks, title="make sim-smoke"):
    """Markdown table of the checks and the overall verdict."""
    lines = [f"## {title}", "", "| Check | Result | Measured |", "|---|---|---|"]
    lines += [
        f"| {c.name} | {'PASS' if c.ok else 'FAIL'} | {c.detail} |" for c in checks
    ]
    passed = sum(c.ok for c in checks)
    verdict = "ALL PASSED" if checks and passed == len(checks) else "FAILED"
    lines += ["", f"{passed}/{len(checks)} checks passed: {verdict}"]
    return "\n".join(lines)
