"""Unit tests of hold_checks (no ROS, no PX4, no Gazebo)."""

import math

import pytest
from sim_smoke.hold_checks import (
    HOLD_SEC,
    MAX_HEADING_DEG,
    RESUME_SEC,
    active_check,
    box_checks,
    commanding_check,
    displacement,
    heading_check,
    hold_checks,
    neutral_mode_check,
    resume_check,
)


def still(z=1.0, seconds=HOLD_SEC, hz=10.0, x=0.0, y=0.0):
    """Ground-truth samples of a drone that does not move."""
    count = int(seconds * hz) + 1
    return [(i / hz, x, y, z) for i in range(count)]


def by_name(checks):
    return {c.name.split(" (")[0]: c for c in checks}


def test_a_still_drone_passes():
    checks = hold_checks(still(z=1.02))
    assert [c.ok for c in checks] == [True, True, True, True], checks


def test_drift_inside_the_limits_passes():
    samples = still()
    samples[300] = (30.0, 0.12, -0.16, 1.10)  # 0.20 m and +0.10 m
    checks = by_name(hold_checks(samples))
    assert checks["horizontal from the start point"].ok
    assert checks["vertical from the start point"].ok
    assert "max 0.200 m" in checks["horizontal from the start point"].detail


@pytest.mark.parametrize(
    "point, failing",
    [
        ((0.26, 0.0, 1.0), "horizontal from the start point"),
        ((0.0, -0.26, 1.0), "horizontal from the start point"),
        ((0.0, 0.0, 1.16), "vertical from the start point"),
        ((0.0, 0.0, 0.84), "vertical from the start point"),
    ],
)
def test_beyond_a_limit_fails(point, failing):
    samples = still()
    samples[200] = (20.0, *point)
    checks = by_name(hold_checks(samples))
    assert not checks[failing].ok
    others = [c for name, c in checks.items() if name != failing]
    assert all(c.ok for c in others), others


def test_the_start_point_is_the_first_sample_not_the_origin():
    # Offset in the world, still: nothing moved from the start point
    checks = hold_checks(still(x=5.0, y=-3.0, z=0.95))
    assert all(c.ok for c in checks), checks


@pytest.mark.parametrize("z", [0.89, 1.11, 2.0])
def test_the_hold_must_start_at_1_m(z):
    assert not by_name(hold_checks(still(z=z)))["start height"].ok


def test_too_short_or_with_a_gap_fails():
    held = "held for 60 s, truth without gaps"
    assert not by_name(hold_checks(still(seconds=59.0)))[held].ok
    samples = still()
    del samples[100:110]  # a 1.1 s hole: the drone was not watched
    assert not by_name(hold_checks(samples))[held].ok


def test_no_samples_fails():
    (check,) = hold_checks([])
    assert not check.ok and "0 truth samples" in check.detail


def test_shorter_hold_for_a_quick_run():
    assert all(c.ok for c in hold_checks(still(seconds=10.0), hold_sec=10.0))


def test_active_check():
    active = {"active": True}
    assert active_check([active, active]).ok
    assert not active_check([active, {"active": False}]).ok
    assert not active_check([]).ok
    assert "1 of 2" in active_check([active, {"active": False}]).detail


def test_displacement():
    samples = [(0.0, 0.0, 0.0, 1.0), (5.0, 0.3, 0.4, 0.9)]
    horizontal, vertical = displacement(samples)
    assert horizontal == pytest.approx(0.5)
    assert vertical == pytest.approx(-0.1)


def test_a_nan_truth_sample_fails_the_hold():
    samples = still()
    samples[300] = (30.0, math.nan, math.nan, math.nan)
    (check,) = hold_checks(samples)
    assert not check.ok and "sample 300 is not finite" in check.detail


# ---------- who held (F19) ----------
FLYING = {"override_active": True}
ENGAGED = {"mode": "ENGAGED", "intent": "NEUTRAL"}
ATTITUDE = (True, False, False)
POSITION = (False, True, False)


def test_commanding_check_passes_when_the_controller_holds():
    check = commanding_check([FLYING] * 3, [ENGAGED] * 3, [14] * 3, [ATTITUDE] * 3)
    assert check.ok, check


@pytest.mark.parametrize(
    "lists, why",
    [
        (([FLYING, {"override_active": False}], [ENGAGED], [14], [ATTITUDE]), "flight"),
        (
            (
                [FLYING],
                [ENGAGED, {"mode": "WAITING", "intent": "NEUTRAL"}],
                [14],
                [ATTITUDE],
            ),
            "mission",
        ),
        (
            ([FLYING], [{"mode": "ENGAGED", "intent": "MOVE_RIGHT"}], [14], [ATTITUDE]),
            "mission",
        ),
        (([FLYING], [ENGAGED], [14, 4], [ATTITUDE]), "PX4 Offboard"),
        (([FLYING], [ENGAGED], [14], [ATTITUDE, POSITION]), "attitude"),
        (([], [ENGAGED], [14], [ATTITUDE]), "flight override_active 0/0"),
    ],
)
def test_commanding_check_fails_when_px4_or_neutral_held(lists, why):
    # PX4's own hold or flight_interface's neutral would hold the drone just
    # as well: the hold test must see who did it
    check = commanding_check(*lists)
    assert not check.ok and why in check.detail


def test_neutral_mode_check():
    assert neutral_mode_check([POSITION] * 5).ok
    assert not neutral_mode_check([POSITION, ATTITUDE]).ok
    assert not neutral_mode_check([POSITION, (False, False, True)]).ok  # velocity
    assert not neutral_mode_check([]).ok


def test_box_checks_from_the_first_sample():
    samples = still(z=1.4, seconds=20.0)
    samples[50] = (5.0, 0.2, 0.1, 1.52)  # 0.22 m and +0.12 m: inside
    assert all(c.ok for c in box_checks(samples, "neutral"))
    samples[60] = (6.0, 0.3, 0.0, 1.4)
    horizontal, vertical = box_checks(samples, "neutral")
    assert not horizontal.ok and vertical.ok
    assert not box_checks(samples[:1], "neutral")[0].ok


def test_heading_check():
    level = [(i * 0.1, 0.5) for i in range(10)]
    assert heading_check(level).ok
    turned = level + [(1.0, 0.5 + math.radians(MAX_HEADING_DEG + 1.0))]
    assert not heading_check(turned).ok
    wrapped = [(0.0, math.radians(179.0)), (0.1, math.radians(-179.0))]
    assert heading_check(wrapped).ok
    assert not heading_check([(0.0, math.nan), (0.1, 0.0)]).ok


def test_resume_check():
    active = {"active": True}
    inactive = {"active": False}
    assert resume_check([(10.0, inactive), (10.5, active)], 10.0).ok
    assert not resume_check(
        [(10.0, inactive), (10.0 + RESUME_SEC + 0.1, active)], 10.0
    ).ok
    assert not resume_check([(10.0, inactive)], 10.0).ok
