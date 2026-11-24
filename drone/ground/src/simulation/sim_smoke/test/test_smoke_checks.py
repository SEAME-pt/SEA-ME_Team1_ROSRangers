"""Unit tests of smoke_checks (no ROS, no PX4)."""

import math

import pytest
from sim_smoke.smoke_checks import (
    MASK_AXES,
    SMOKE_MASKS,
    Check,
    body_displacement,
    first_zero_after,
    hover_check,
    is_zero,
    limits_check,
    loss_check,
    mask_attitude_check,
    mask_targets_check,
    measured_speed_check,
    movement_check,
    owned_axes,
    rate_check,
    summary,
)

NORTH, EAST = 0.0, math.pi / 2


def ticks(hz, seconds, start=0.0):
    return [start + i / hz for i in range(int(hz * seconds) + 1)]


# ---------- helpers ----------
def test_is_zero():
    assert is_zero((0.0, 0.0, -0.0, 0.0))
    assert not is_zero((0.0, 0.01, 0.0, 0.0))


def test_owned_axes_rejects_unknown_mask():
    assert owned_axes(11) == {"forward", "left", "yaw"}
    with pytest.raises(ValueError):
        owned_axes(7)


# ---------- rate ----------
def test_rate_20hz_passes():
    check = rate_check(ticks(20, 10))
    assert check.ok, check.detail
    assert "20.0 Hz" in check.detail


@pytest.mark.parametrize("hz", [10, 25])
def test_rate_off_target_fails(hz):
    assert not rate_check(ticks(hz, 10)).ok


def test_rate_with_a_stall_fails():
    stamps = ticks(20, 5) + ticks(20, 5, start=5.4)
    check = rate_check(stamps)
    assert not check.ok
    assert "400 ms" in check.detail


@pytest.mark.parametrize("stamps", [[], [1.0], [1.0, 1.0]])
def test_rate_needs_two_distinct_stamps(stamps):
    assert not rate_check(stamps).ok


# ---------- limits ----------
def test_limits_within_mission_limits():
    targets = [(0.0, -0.3, 0.0, 0.0), (0.0, 0.0, 0.2, math.radians(20))]
    assert limits_check(targets).ok


@pytest.mark.parametrize(
    "target",
    [
        (0.25, -0.25, 0.0, 0.0),  # diagonal: 0.35 m/s as a whole
        (0.0, 0.0, 0.21, 0.0),
        (0.0, 0.0, 0.0, math.radians(21)),
    ],
)
def test_limits_above_a_limit_fails(target):
    assert not limits_check([target]).ok


def test_limits_without_targets_fails():
    assert not limits_check([]).ok


def test_measured_speed():
    assert measured_speed_check([(0.3, 0.1, -0.2)]).ok
    assert not measured_speed_check([(0.4, 0.4, 0.0)]).ok
    assert not measured_speed_check([(0.0, 0.0, 0.6)]).ok
    assert not measured_speed_check([]).ok


# ---------- mask ----------
def test_mask_targets():
    climb = (0.0, 0.0, 0.2, 0.0)
    right = (0.0, -0.3, 0.0, 0.0)
    assert mask_targets_check([climb, right], 15).ok
    assert mask_targets_check([right], 11).ok
    check = mask_targets_check([right, climb], 11)
    assert not check.ok and check.detail.startswith("1 of 2")
    assert not mask_targets_check([right], 8).ok
    assert not mask_targets_check([], 15).ok


HOVER = 0.6


def attitude(roll=0.0, pitch=0.0, yaw_rate=0.0, throttle=HOVER, active=True):
    return {
        "roll": roll,
        "pitch": pitch,
        "yaw_rate": yaw_rate,
        "throttle": throttle,
        "active": active,
        "reason": "ok" if active else "stale_velocity",
    }


def test_mask_attitude():
    tilt = attitude(roll=-3.0, pitch=2.0)
    climb = attitude(throttle=0.68)
    yaw = attitude(yaw_rate=10.0)
    assert mask_attitude_check([tilt, climb, yaw], 15, HOVER).ok
    assert mask_attitude_check([tilt, yaw], 11, HOVER).ok
    assert not mask_attitude_check([climb], 11, HOVER).ok  # throttle not owned
    assert mask_attitude_check([yaw], 8, HOVER).ok
    assert not mask_attitude_check([attitude(roll=1.0)], 8, HOVER).ok
    assert not mask_attitude_check([attitude(pitch=1.0)], 8, HOVER).ok


def test_mask_attitude_counts_only_active_setpoints():
    # An inactive setpoint commands nothing (F8), whatever its numbers
    ignored = attitude(roll=5.0, throttle=0.9, active=False)
    check = mask_attitude_check([attitude(), ignored], 8, HOVER)
    assert check.ok
    assert "0 of 1 active" in check.detail and "1 inactive" in check.detail


def test_mask_attitude_needs_an_active_setpoint():
    assert not mask_attitude_check([], 11, HOVER).ok
    assert not mask_attitude_check([attitude(active=False)], 11, HOVER).ok


# ---------- direction ----------
@pytest.mark.parametrize(
    "yaw, end, expected",
    [
        (NORTH, (1.0, 0.0, 0.0), (1.0, 0.0, 0.0)),  # north = forward
        (NORTH, (0.0, 1.0, 0.0), (0.0, -1.0, 0.0)),  # east = right
        (EAST, (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),  # facing east: east = forward
        (EAST, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),  # facing east: north = left
        (EAST, (0.0, 0.0, -0.5), (0.0, 0.0, 0.5)),  # down negative = up
    ],
)
def test_body_displacement(yaw, end, expected):
    got = body_displacement((0.0, 0.0, 0.0), end, yaw)
    assert got == pytest.approx(expected, abs=1e-9)


def test_movement_right_passes():
    check = movement_check("right", (0.05, -0.9, 0.02), "left", -1, 15)
    assert check.ok, check.detail


@pytest.mark.parametrize(
    "displacement",
    [
        (0.0, 0.9, 0.0),  # wrong way
        (0.0, -0.1, 0.0),  # too little
        (0.4, -0.9, 0.0),  # drifted forward too
    ],
)
def test_movement_right_fails(displacement):
    assert not movement_check("right", displacement, "left", -1, 15).ok


def test_movement_outside_mask_must_stay_still():
    still = movement_check("climb, mask 11", (0.0, 0.05, 0.1), "up", 1, 11)
    assert still.ok and "not in mask 11" in still.detail
    assert not movement_check("climb, mask 11", (0.0, 0.0, 0.6), "up", 1, 11).ok


# ---------- loss ----------
def test_first_zero_after():
    samples = [(1.0, (0.0,)), (2.0, (0.3,)), (3.0, (0.0,))]
    assert first_zero_after(samples, 1.5) == 3.0
    assert first_zero_after(samples, 3.0) is None


def moving_then_zero(t_zero, end=12.0):
    out = []
    for t in ticks(20, end):
        out.append((t, (0.0, 0.0, 0.0, 0.0) if t >= t_zero else (0.0, -0.3, 0.0, 0.0)))
    return out


def test_loss_within_limits_passes():
    check = loss_check(10.0, moving_then_zero(10.2))
    assert check.ok, check
    assert "200 ms" in check.detail


def test_loss_too_slow_fails():
    assert not loss_check(10.0, moving_then_zero(10.3)).ok


def test_loss_never_zero_fails():
    check = loss_check(10.0, moving_then_zero(99.0))
    assert not check.ok and check.detail == "never zero"


def test_loss_without_intent_or_movement_fails():
    assert not loss_check(None, []).ok
    still = [(t, (0.0, 0.0, 0.0, 0.0)) for t in ticks(20, 12)]
    assert not loss_check(10.0, still).ok


def test_hover_still_passes():
    assert hover_check([(0.05, -0.05, 0.08), (0.0, 0.0, -0.1)]).ok


@pytest.mark.parametrize(
    "velocity",
    [(0.2, 0.0, 0.0), (0.08, 0.08, 0.0), (0.0, 0.0, 0.15), (0.0, 0.0, -0.15)],
)
def test_hover_moving_fails(velocity):
    # A vertical runaway after the loss fails too, not only a horizontal one
    assert not hover_check([(0.0, 0.0, 0.0), velocity]).ok


def test_hover_without_samples_fails():
    assert not hover_check([]).ok


def test_smoke_masks_fly_right_and_up():
    # The scenario only flies right and up: every smoke mask owns "left"
    for mask in SMOKE_MASKS:
        assert "left" in MASK_AXES[mask]
    assert 8 not in SMOKE_MASKS


# ---------- summary ----------
def test_summary():
    text = summary([Check("a", True, "x"), Check("b", False, "y")])
    assert "| a | PASS | x |" in text
    assert "| b | FAIL | y |" in text
    assert text.endswith("1/2 checks passed: FAILED")
    assert summary([Check("a", True, "x")]).endswith("ALL PASSED")
    assert summary([]).endswith("FAILED")
