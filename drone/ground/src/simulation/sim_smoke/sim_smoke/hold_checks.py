"""Checks of the SITL hold test (make sim-hold, #199 Phase 8), with NO ROS imports.

REQ-NAV-001 (Requirements.md): "at 1.0 m, with zero velocity setpoints from
the flight interface, the drone stays within 0.25 m horizontally and +-0.15 m
vertically of the start point for 60 s", verified with Gazebo ground truth
(ADR-025 F26). hold_scenario.py records; these functions judge, so the rules
are unit-tested on any machine.

Positions are Gazebo's world pose of the model (x, y, z in metres, z up from
the ground plane): the truth, not PX4's estimate. Every check returns a
smoke_checks.Check (name, ok, detail).
"""

import math

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


def displacement(samples):
    """samples: [(t, x, y, z)]. (horizontal, vertical) from the first to the
    last, in metres: for comparing PX4's estimate with the truth."""
    _, x0, y0, z0 = samples[0]
    _, x1, y1, z1 = samples[-1]
    return math.hypot(x1 - x0, y1 - y0), z1 - z0
