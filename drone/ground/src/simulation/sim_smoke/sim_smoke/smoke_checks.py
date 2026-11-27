"""Checks of the PX4 SITL smoke test (make sim-smoke, #86, #199), with NO ROS imports.

smoke_scenario.py records what the chain did; these functions judge it, so the
pass/fail rules are unit-tested on any machine. Every check returns a Check:
name, ok, and a one-line detail with the measured values.

    REQ-SW-001   /mission/velocity_target at 20 Hz
    REQ-CTL-001  commanded speeds within the mission limits; measured speed
                 within the hard ceiling
    REQ-SAF-017  mission_node's zero target within 0.25 s of losing
                 /gesture/intent, then the drone hovers
    REQ-SAF-018  no axis outside the override mask commanded (velocity target
                 and attitude setpoint) or moving
    direction    the drone moves the way the gesture says, and only that way
                 (the heading included)

A sample with a non-finite number (NaN, inf) fails the check that reads it,
naming the sample: Python's max() would silently skip a NaN.

Since #199 the ground's controller flies PX4 with attitude setpoints
(/control/attitude_setpoint -> flight_interface's px4 backend). A zero target
is "hold zero velocity" (ADR-025 F10), so PX4 never gets a zero velocity
setpoint: REQ-SAF-017 is judged at mission_node's output and by the measured
hover, REQ-SAF-018 on /control/attitude_setpoint and by the measured drift
(F25).
"""

import math
from collections import namedtuple

Check = namedtuple("Check", "name ok detail")

# mission_node defaults (REQ-CTL-001) and the hard ceiling of mission_node and
# the controller's input clamp (F9). EPS absorbs float noise in the published
# values.
LIMIT_LATERAL_MPS = 0.3
LIMIT_VERTICAL_MPS = 0.2
LIMIT_YAW_DPS = 20.0
CEILING_LINEAR_MPS = 0.5
EPS = 1e-6

RATE_HZ = 20.0
RATE_TOLERANCE = 0.1  # 18-22 Hz
MAX_GAP_SEC = 0.15  # three ticks: one late tick is fine, a stall is not

LOSS_LIMIT_SEC = 0.25  # REQ-SAF-017, at the mission_node output

# Body axes (FLU) per override mask, as in mission_node.
AXES = ("forward", "left", "up", "yaw")
MASK_AXES = {
    8: {"yaw"},
    11: {"forward", "left", "yaw"},
    15: {"forward", "left", "up", "yaw"},
}
# The scenario moves only right and up, so under mask 8 (yaw only) nothing is
# ever commanded and the REQ-SAF-017 check cannot pass: make sim-smoke refuses it.
SMOKE_MASKS = (11, 15)

# After the loss the drone must hover: every measured axis at most this.
HOVER_MPS = 0.1

# Movement: the commanded axis must move at least MIN_MOVE_M the right way;
# every other axis (and an axis outside the mask) at most MAX_DRIFT_M.
MIN_MOVE_M = 0.3
MAX_DRIFT_M = 0.25
# No move commands yaw: the heading may change at most this (deg).
MAX_HEADING_DEG = 10.0


def first_non_finite(samples):
    """Index of the first sample with a non-finite number, or None.
    samples: sequences of numbers. Example: [(0.1, 0), (nan, 0)] -> 1."""
    for index, sample in enumerate(samples):
        for value in sample:
            if not math.isfinite(value):
                return index
    return None


def non_finite_check(name, samples):
    """A failed Check naming the first non-finite sample, or None if all are
    finite."""
    index = first_non_finite(samples)
    if index is None:
        return None
    return Check(name, False, f"sample {index} is not finite: {tuple(samples[index])}")


def heading_change_deg(start, end):
    """The smallest turn (deg) from heading `start` to `end` (rad).
    Example: 179 deg -> -179 deg is 2 deg, not 358."""
    turn = (end - start + math.pi) % (2.0 * math.pi) - math.pi
    return abs(math.degrees(turn))


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
    bad = non_finite_check("commanded speed (REQ-CTL-001)", targets)
    if bad is not None:
        return bad
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
    bad = non_finite_check("measured speed (REQ-CTL-001 ceiling)", velocities)
    if bad is not None:
        return bad
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


def mask_attitude_check(setpoints, mask, hover_throttle):
    """setpoints: the /control/attitude_setpoint dicts (roll, pitch, yaw_rate,
    throttle, active). Only the active ones command (ADR-025 F8).

    An axis outside the mask is level: pitch 0 without "forward", roll 0
    without "left", yaw_rate 0 without "yaw", and the throttle exactly the
    hover throttle without "up" (mask 8 and 11). The controller writes these
    values as they are, so they are compared exactly. Example, mask 11, hover
    0.6: (roll -3, pitch 0, yaw_rate 0, throttle 0.6) is fine; throttle 0.65
    is not: the ground does not own throttle there.
    """
    owned = owned_axes(mask)
    active = [sp for sp in setpoints if sp.get("active") is True]

    def outside(sp):
        pitch = "forward" not in owned and sp.get("pitch") != 0.0
        roll = "left" not in owned and sp.get("roll") != 0.0
        yaw = "yaw" not in owned and sp.get("yaw_rate") != 0.0
        throttle = "up" not in owned and sp.get("throttle") != hover_throttle
        return pitch or roll or yaw or throttle

    bad = sum(1 for sp in active if outside(sp))
    ok = bool(active) and bad == 0
    return Check(
        f"attitude setpoints outside mask {mask} (REQ-SAF-018)",
        ok,
        f"{bad} of {len(active)} active setpoints command an axis outside the "
        f"mask ({len(setpoints) - len(active)} inactive)",
    )


# ---------- direction ----------
def body_displacement(start, end, yaw):
    """NED positions (n, e, d) and the PX4 heading (rad, from north clockwise)
    -> (forward, left, up) in metres, in the body frame at the start."""
    dn, de, dd = (b - a for a, b in zip(start, end))
    forward = dn * math.cos(yaw) + de * math.sin(yaw)
    right = -dn * math.sin(yaw) + de * math.cos(yaw)
    return forward, -right, -dd


def movement_check(name, displacement, axis, sign, mask, turned_deg=0.0):
    """The commanded axis moved MIN_MOVE_M the right way and nothing else drifted.

    displacement: (forward, left, up). axis: "forward", "left" or "up".
    sign: +1 or -1. An axis outside the mask must not move at all (REQ-SAF-018).
    turned_deg: how far the heading changed (heading_change_deg); no move
    commands yaw, so more than MAX_HEADING_DEG fails.
    """
    bad = non_finite_check(name, [tuple(displacement) + (turned_deg,)])
    if bad is not None:
        return bad
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
    turned_ok = turned_deg <= MAX_HEADING_DEG
    parts.append(f"heading {turned_deg:.1f} deg")
    rule += f", heading <= {MAX_HEADING_DEG:.0f} deg"
    return Check(name, ok and turned_ok, f"{', '.join(parts)} ({rule})")


# ---------- REQ-SAF-017 ----------
def first_zero_after(samples, t0):
    """samples: (t, values). Time of the first all-zero sample after t0, or None."""
    for t, values in samples:
        if t > t0 and is_zero(values):
            return t
    return None


def loss_check(last_intent, targets):
    """Zero target after /gesture/intent stops (REQ-SAF-017, F25).

    last_intent: receive time of the last /gesture/intent before intent_node was
    killed. targets: (t, values) of /mission/velocity_target, t = the PUBLISH
    stamp (header.stamp), on the same wall clock. The delivery delay of the
    zero to the test is not counted: it is not mission_node's. The drone
    itself is judged by hover_check: the controller then holds zero velocity.
    """
    name = "zero after losing /gesture/intent (REQ-SAF-017): mission_node output"
    if last_intent is None:
        return Check(name, False, "no /gesture/intent received")
    before = [v for t, v in targets if t <= last_intent]
    if not before or is_zero(before[-1]):
        # The last target before the loss must be a move: otherwise a zero
        # that was already there would pass as the reaction to the loss
        return Check(name, False, "not moving at the loss (last target before it zero)")
    t_zero = first_zero_after(targets, last_intent)
    if t_zero is None:
        return Check(name, False, "never zero")
    moved_again = [t for t, v in targets if t > t_zero and not is_zero(v)]
    if moved_again:
        return Check(
            name,
            False,
            f"zero at +{(t_zero - last_intent) * 1000:.0f} ms, then a move again "
            f"at +{(moved_again[0] - last_intent) * 1000:.0f} ms",
        )
    delay = t_zero - last_intent
    return Check(
        name,
        delay <= LOSS_LIMIT_SEC,
        f"{delay * 1000:.0f} ms (<= {LOSS_LIMIT_SEC * 1000:.0f} ms)",
    )


def hover_check(velocities):
    """velocities: (v_north, v_east, v_down) PX4 measured, some time after the
    loss. The drone hovers: horizontal AND vertical speed <= HOVER_MPS."""
    name = "hovers after the loss (REQ-SAF-017)"
    if not velocities:
        return Check(name, False, "no PX4 velocity")
    bad = non_finite_check(name, velocities)
    if bad is not None:
        return bad
    horizontal = max(math.hypot(v[0], v[1]) for v in velocities)
    vertical = max(abs(v[2]) for v in velocities)
    ok = horizontal <= HOVER_MPS and vertical <= HOVER_MPS
    detail = (
        f"max horizontal {horizontal:.2f} m/s, vertical {vertical:.2f} m/s "
        f"(<= {HOVER_MPS}) from 2.5 s after the kill, {len(velocities)} samples"
    )
    return Check(name, ok, detail)


def hover_drift_check(positions):
    """positions: (north, east, down) PX4 measured over the hover window
    after the loss. The drone stays within MAX_DRIFT_M of the first one,
    horizontally and vertically (REQ-SAF-017: it hovers, it does not creep)."""
    name = "stays put after the loss (REQ-SAF-017)"
    if len(positions) < 2:
        return Check(name, False, f"{len(positions)} PX4 positions")
    bad = non_finite_check(name, positions)
    if bad is not None:
        return bad
    n0, e0, d0 = positions[0]
    horizontal = max(math.hypot(n - n0, e - e0) for n, e, _ in positions)
    vertical = max(abs(d - d0) for _, _, d in positions)
    ok = horizontal <= MAX_DRIFT_M and vertical <= MAX_DRIFT_M
    detail = (
        f"max {horizontal:.2f} m horizontal, {vertical:.2f} m vertical "
        f"(<= {MAX_DRIFT_M}) over {len(positions)} samples"
    )
    return Check(name, ok, detail)


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
