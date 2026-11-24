"""Unit tests of hold_checks (no ROS, no PX4, no Gazebo)."""

import pytest
from sim_smoke.hold_checks import (
    HOLD_SEC,
    active_check,
    displacement,
    hold_checks,
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
