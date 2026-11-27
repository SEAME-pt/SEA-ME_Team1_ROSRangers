"""Checks of the SITL hold test (make sim-hold, #199 Phase 8), with NO ROS imports.

REQ-NAV-001 (Requirements.md): "at 1.0 m, with zero velocity setpoints from
the flight interface, the drone stays within 0.25 m horizontally and +-0.15 m
vertically of the start point for 60 s", verified with Gazebo ground truth
(ADR-025 F26). hold_scenario.py records; these functions judge, so the rules
are unit-tested on any machine.

Positions are Gazebo's world pose of the model (x, y, z in metres, z up from
the ground plane): the truth, not PX4's estimate. Every check returns a
smoke_checks.Check (name, ok, detail). A non-finite sample fails the check
that reads it.

Who holds (ADR-025 F19): during the 60 s the CONTROLLER must hold (every
/flight/state override_active, mission ENGAGED with intent NEUTRAL, PX4 in
Offboard, attitude setpoints), not PX4's own hold. Then the neutral phase
(#199, 2026-11-26): the controller is paused (hover_throttle 0.0), so
flight_interface sends neutral and PX4 holds the frozen point
(px4_logic.NeutralHold) in position Offboard; it must stay within the same
REQ-NAV-001 box around where it was at the pause, watched without gaps for
the whole phase, and the controller must fly again once resumed.
"""

import math

from sim_smoke import smoke_checks as sc
from sim_smoke.smoke_checks import Check

HOLD_SEC = 60.0  # REQ-NAV-001
HORIZONTAL_M = 0.25  # REQ-NAV-001, from the start point
VERTICAL_M = 0.15  # REQ-NAV-001, +- from the start point
TARGET_HEIGHT_M = 1.0  # REQ-NAV-001 "at 1.0 m"
# How close to 1.0 m the hold must start. The descent is flown by gesture
# (0.2 m/s), so it stops within a few cm; 0.10 m is "at 1.0 m" with room.
START_HEIGHT_TOL_M = 0.10
# A gap in the ground truth longer than this means the drone was not watched:
# the check cannot say it stayed inside.
MAX_GAP_SEC = 0.5
# Nothing commands yaw during the hold: the heading may change at most this.
MAX_HEADING_DEG = 10.0
# PX4's NAVIGATION_STATE_OFFBOARD (px4_msgs VehicleStatus)
NAV_STATE_OFFBOARD = 14
# The neutral phase: the controller paused this long; judged from
# NEUTRAL_SETTLE_SEC after the pause (the switch takes a few ticks).
NEUTRAL_SEC = 20.0
NEUTRAL_SETTLE_SEC = 0.5
# After the resume, an active setpoint must come within this.
RESUME_SEC = 2.0


def hold_checks(samples, hold_sec=HOLD_SEC):
    """samples: [(t, x, y, z)] of the ground truth during the hold, t in s,
    increasing. The first sample is the start point.

    Returns the REQ-NAV-001 checks: the start height, the duration (and no
    gap in the truth), the largest horizontal distance and the largest
    vertical deviation from the start point.

    Example: start (0, 0, 1.02); a sample at (0.12, -0.16, 1.10) is
    hypot(0.12, 0.16) = 0.20 m horizontally (<= 0.25) and +0.08 m vertically
    (<= 0.15) from it.
    """
    if len(samples) < 2:
        return [Check("hold (REQ-NAV-001)", False, f"{len(samples)} truth samples")]
    bad = sc.non_finite_check("hold (REQ-NAV-001)", samples)
    if bad is not None:
        return [bad]
    t0, x0, y0, z0 = samples[0]
    checks = []

    off = abs(z0 - TARGET_HEIGHT_M)
    checks.append(
        Check(
            "start height (REQ-NAV-001: at 1.0 m)",
            off <= START_HEIGHT_TOL_M,
            f"{z0:.2f} m (1.0 +- {START_HEIGHT_TOL_M} m)",
        )
    )

    span = samples[-1][0] - t0
    gap = max(b[0] - a[0] for a, b in zip(samples, samples[1:]))
    checks.append(
        Check(
            "held for 60 s, truth without gaps",
            span >= hold_sec and gap <= MAX_GAP_SEC,
            f"{span:.1f} s (>= {hold_sec:.0f}), largest gap {gap * 1000:.0f} ms "
            f"(<= {MAX_GAP_SEC * 1000:.0f}), {len(samples)} samples",
        )
    )

    horizontal = 0.0
    up = 0.0
    down = 0.0
    for _, x, y, z in samples:
        horizontal = max(horizontal, math.hypot(x - x0, y - y0))
        up = max(up, z - z0)
        down = max(down, z0 - z)
    checks.append(
        Check(
            "horizontal from the start point (REQ-NAV-001)",
            horizontal <= HORIZONTAL_M,
            f"max {horizontal:.3f} m (<= {HORIZONTAL_M})",
        )
    )
    checks.append(
        Check(
            "vertical from the start point (REQ-NAV-001)",
            up <= VERTICAL_M and down <= VERTICAL_M,
            f"max +{up:.3f} / -{down:.3f} m (+-{VERTICAL_M})",
        )
    )
    return checks


def active_check(setpoints):
    """setpoints: the /control/attitude_setpoint dicts during the hold. The
    hold is the controller's (zero velocity, F10): every one must be active,
    or PX4's own hold (F19) did the work."""
    active = sum(1 for sp in setpoints if sp.get("active") is True)
    total = len(setpoints)
    return Check(
        "controller active during the hold",
        total > 0 and active == total,
        f"{active} of {total} setpoints active",
    )


def nearest_sample(samples, t):
    """The (t, x, y, z) sample closest in time to t, or None if none is
    within MAX_GAP_SEC of it (the truth was not watched then)."""
    best = None
    for sample in samples:
        if best is None or abs(sample[0] - t) < abs(best[0] - t):
            best = sample
    if best is None or abs(best[0] - t) > MAX_GAP_SEC:
        return None
    return best


def box_checks(samples, label, origin, t_from, t_to):
    """samples: [(t, x, y, z)] between t_from and t_to; origin: the
    (t, x, y, z) sample the box is centred on (the position at the pause,
    where PX4 froze its hold point), or None if there was none.

    Returns three checks (label says which phase):
      * coverage: samples from t_from to t_to with no gap over MAX_GAP_SEC,
        at the start, inside or at the end (2 samples are not enough);
      * horizontal <= HORIZONTAL_M and vertical within +-VERTICAL_M of the
        origin (REQ-NAV-001's box).
    Example: pause at (0, 0, 1.40); a sample at (0.15, 0.20, 1.52) is
    hypot(0.15, 0.20) = 0.25 m (inside) and +0.12 m (inside)."""
    name_h = f"{label}: horizontal from the pause point"
    if origin is None:
        return [Check(label, False, "no truth sample at the pause")]
    if len(samples) < 2:
        return [Check(label, False, f"{len(samples)} truth samples")]
    bad = sc.non_finite_check(label, samples + [origin])
    if bad is not None:
        return [bad]
    _, x0, y0, z0 = origin
    times = [s[0] for s in samples]
    gaps = [b - a for a, b in zip(times, times[1:])]
    gaps.append(times[0] - t_from)  # not watched at the start
    gaps.append(t_to - times[-1])  # not watched at the end
    largest_gap = max(gaps)
    horizontal = max(math.hypot(x - x0, y - y0) for _, x, y, _ in samples)
    up = max(z - z0 for _, _, _, z in samples)
    down = max(z0 - z for _, _, _, z in samples)
    span = times[-1] - times[0]
    return [
        Check(
            f"{label}: truth without gaps",
            largest_gap <= MAX_GAP_SEC,
            f"{span:.1f} s of {t_to - t_from:.1f} s, largest gap "
            f"{largest_gap * 1000:.0f} ms (<= {MAX_GAP_SEC * 1000:.0f}), "
            f"{len(samples)} samples",
        ),
        Check(
            name_h,
            horizontal <= HORIZONTAL_M,
            f"max {horizontal:.3f} m (<= {HORIZONTAL_M}) over {span:.1f} s",
        ),
        Check(
            f"{label}: vertical from the pause point",
            up <= VERTICAL_M and down <= VERTICAL_M,
            f"max +{up:.3f} / -{down:.3f} m (+-{VERTICAL_M})",
        ),
    ]


def heading_check(yaws, label="heading during the hold"):
    """yaws: [(t, yaw rad)] of the ground truth. Nothing commands yaw, so the
    heading stays within MAX_HEADING_DEG of the first (wrapped at +-180)."""
    if len(yaws) < 2:
        return Check(label, False, f"{len(yaws)} samples")
    bad = sc.non_finite_check(label, yaws)
    if bad is not None:
        return bad
    yaw0 = yaws[0][1]
    turned = max(sc.heading_change_deg(yaw0, yaw) for _, yaw in yaws)
    return Check(
        label,
        turned <= MAX_HEADING_DEG,
        f"max {turned:.1f} deg (<= {MAX_HEADING_DEG:.0f})",
    )


def _all(items, rule):
    """(count ok, total, index of the first that is not)."""
    first_bad = None
    count = 0
    for index, item in enumerate(items):
        if rule(item):
            count += 1
        elif first_bad is None:
            first_bad = index
    return count, len(items), first_bad


def commanding_check(flights, missions, nav_states, modes):
    """Over the hold window, the controller held the drone, not PX4 (F19):
    every /flight/state has override_active true, every /mission/state is
    ENGAGED with intent NEUTRAL, every PX4 nav_state is Offboard and every
    OffboardControlMode is attitude (not position or velocity). Each list
    must be non-empty. modes: (attitude, position, velocity) booleans."""
    rules = (
        ("flight override_active", flights, lambda f: f.get("override_active") is True),
        (
            "mission ENGAGED/NEUTRAL",
            missions,
            lambda m: m.get("mode") == "ENGAGED" and m.get("intent") == "NEUTRAL",
        ),
        ("PX4 Offboard", nav_states, lambda n: n == NAV_STATE_OFFBOARD),
        ("attitude Offboard mode", modes, lambda m: tuple(m) == (True, False, False)),
    )
    parts = []
    ok = True
    for what, items, rule in rules:
        count, total, first_bad = _all(items, rule)
        parts.append(f"{what} {count}/{total}")
        if total == 0 or first_bad is not None:
            ok = False
            if first_bad is not None:
                parts[-1] += f" (first bad: {items[first_bad]})"
    return Check("controller commanding during the hold (F19)", ok, ", ".join(parts))


def neutral_mode_check(modes):
    """modes: (attitude, position, velocity) during the neutral phase. PX4
    holds the frozen point: every one is position Offboard."""
    count, total, first_bad = _all(modes, lambda m: tuple(m) == (False, True, False))
    detail = f"{count} of {total} position Offboard"
    if first_bad is not None:
        detail += f" (first bad: {tuple(modes[first_bad])})"
    return Check(
        "neutral: PX4 holds a position (NeutralHold)",
        total > 0 and first_bad is None,
        detail,
    )


def resume_check(setpoints, t_resume):
    """setpoints: [(t, /control/attitude_setpoint dict)]. After the resume
    the controller flies again: an active setpoint within RESUME_SEC."""
    times = [
        t - t_resume
        for t, sp in setpoints
        if t >= t_resume and sp.get("active") is True
    ]
    ok = bool(times) and times[0] <= RESUME_SEC
    if times:
        detail = (
            f"first active setpoint {times[0]:.2f} s after the resume (<= {RESUME_SEC})"
        )
    else:
        detail = "no active setpoint after the resume"
    return Check("controller flies again after the neutral phase", ok, detail)


def displacement(samples):
    """samples: [(t, x, y, z)]. (horizontal, vertical) from the first to the
    last, in metres: for comparing PX4's estimate with the truth."""
    _, x0, y0, z0 = samples[0]
    _, x1, y1, z1 = samples[-1]
    return math.hypot(x1 - x0, y1 - y0), z1 - z0
