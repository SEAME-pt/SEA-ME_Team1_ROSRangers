"""Unit tests of px4_logic.py (#199): frames, quaternion, yaw setpoint, the
drone state for the guard, the measured velocity and the simulated pilot. No
ROS, no px4_msgs."""

import math

import pytest

from flight_interface.guard import Setpoint
from flight_interface.px4_logic import (
    HOLD_POSITION_TIMEOUT,
    LAND_TIMEOUT,
    PILOT_VELOCITY_TIMEOUT,
    POSITION_TIMEOUT,
    STATUS_TIMEOUT,
    YAW_LEAD_CAP,
    NeutralHold,
    Px4Logic,
    SimPilot,
    flu_to_frd,
    ned_to_flu,
    quaternion_from_euler,
    wrap_angle,
)

NORTH, EAST, SOUTH, WEST = 0.0, math.pi / 2, math.pi, -math.pi / 2
HEADINGS = (NORTH, EAST, SOUTH, WEST)
HOVER = 0.7


def approx(values):
    return pytest.approx(values, abs=1e-9)


def fresh(mask=15, heading=NORTH, now=0.0):
    logic = Px4Logic(mask)
    logic.on_status(True, True, now)
    logic.on_position(heading, True, now)
    return logic


def euler_from_quaternion(q):
    """Back to (roll, pitch, yaw), Z-Y-X, to check quaternion_from_euler."""
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


# ---------- frames ----------
def test_flu_to_frd_keeps_roll_and_flips_pitch_and_yaw_rate():
    roll, pitch, yaw_rate = flu_to_frd(10.0, 5.0, 20.0)
    assert roll == pytest.approx(math.radians(10.0))
    assert pitch == pytest.approx(math.radians(-5.0))  # nose down -> FRD pitch < 0
    assert yaw_rate == pytest.approx(math.radians(-20.0))  # left -> FRD clockwise < 0


def test_quaternion_examples():
    assert quaternion_from_euler(0, 0, 0) == approx((1, 0, 0, 0))
    half = math.sqrt(0.5)
    assert quaternion_from_euler(0, 0, math.pi / 2) == approx((half, 0, 0, half))
    q = quaternion_from_euler(0.1, -0.2, 2.0)
    assert sum(v * v for v in q) == pytest.approx(1.0)


@pytest.mark.parametrize("heading", HEADINGS)
def test_quaternion_round_trip(heading):
    roll, pitch = math.radians(7.0), math.radians(-12.0)
    back = euler_from_quaternion(quaternion_from_euler(roll, pitch, heading))
    assert back[0] == pytest.approx(roll)
    assert back[1] == pytest.approx(pitch)
    assert wrap_angle(back[2] - heading) == pytest.approx(0.0, abs=1e-9)


def test_wrap_angle():
    assert wrap_angle(math.radians(190)) == pytest.approx(math.radians(-170))
    assert wrap_angle(-math.pi) == pytest.approx(math.pi)
    assert wrap_angle(3 * math.pi) == pytest.approx(math.pi)


@pytest.mark.parametrize(
    "heading, ned, flu",
    [
        (NORTH, (1, 0, 0), (1, 0, 0)),  # facing north, moving north: forward
        (EAST, (0, 1, 0), (1, 0, 0)),  # facing east, moving east: forward
        (NORTH, (0, -1, 0), (0, 1, 0)),  # facing north, moving west: left
        (SOUTH, (0, 1, 0), (0, 1, 0)),  # facing south, moving east: left
        (WEST, (1, 0, 0), (0, -1, 0)),  # facing west, moving north: right
        (NORTH, (0, 0, -0.5), (0, 0, 0.5)),  # climbing: NED down < 0, FLU up > 0
    ],
)
def test_ned_to_flu_on_the_four_headings(heading, ned, flu):
    assert ned_to_flu(*ned, heading) == approx(flu)


# ---------- attitude ----------
@pytest.mark.parametrize("heading", HEADINGS)
def test_forward_is_nose_down_at_any_heading(heading):
    logic = fresh(heading=heading)
    q_d, thrust, yaw_rate = logic.attitude(Setpoint(0.0, 5.0, 0.0, HOVER, True), 0.0)
    roll, pitch, yaw = euler_from_quaternion(q_d)
    assert pitch == pytest.approx(math.radians(-5.0))  # FRD: nose down is negative
    assert roll == pytest.approx(0.0, abs=1e-9)
    assert wrap_angle(yaw - heading) == pytest.approx(0.0, abs=1e-9)  # holds heading
    assert thrust == approx((0.0, 0.0, -HOVER))  # FRD: up is -z
    assert yaw_rate == 0.0


def test_left_is_left_side_down():
    logic = fresh()
    q_d, _, _ = logic.attitude(Setpoint(-4.0, 0.0, 0.0, HOVER, True), 0.0)
    roll, _, _ = euler_from_quaternion(q_d)
    assert roll == pytest.approx(
        math.radians(-4.0)
    )  # roll - = left side down, both frames


def test_yaw_setpoint_follows_the_rate_and_holds_at_zero():
    logic = fresh(heading=NORTH)
    left = Setpoint(0.0, 0.0, 20.0, HOVER, True)  # 20 deg/s to the left
    now = 0.0
    logic.attitude(left, now)
    for _ in range(10):  # 0.2 s at 50 Hz
        now = now + 0.02
        logic.on_position(NORTH, True, now)  # the drone has not turned yet
        q_d, _, yaw_rate = logic.attitude(left, now)
    _, _, yaw = euler_from_quaternion(q_d)
    assert yaw == pytest.approx(
        math.radians(-4.0)
    )  # left = counter-clockwise = yaw < 0
    assert yaw_rate == pytest.approx(math.radians(-20.0))
    hold = Setpoint(0.0, 0.0, 0.0, HOVER, True)
    now = now + 0.02
    logic.on_position(NORTH, True, now)
    q_d, _, _ = logic.attitude(hold, now)
    assert euler_from_quaternion(q_d)[2] == pytest.approx(math.radians(-4.0))


def test_yaw_lead_is_capped():
    logic = fresh(heading=NORTH)
    fast = Setpoint(0.0, 0.0, -45.0, HOVER, True)  # right, the drone does not follow
    now = 0.0
    for _ in range(200):
        logic.on_position(NORTH, True, now)
        q_d, _, _ = logic.attitude(fast, now)
        now = now + 0.02
    assert euler_from_quaternion(q_d)[2] == pytest.approx(YAW_LEAD_CAP)


def test_yaw_setpoint_not_moved_on_a_long_step_and_reset_by_neutral():
    logic = fresh(heading=EAST)
    turn = Setpoint(0.0, 0.0, 20.0, HOVER, True)
    logic.attitude(turn, 0.0)
    logic.on_position(EAST, True, 0.5)
    q_d, _, _ = logic.attitude(turn, 0.5)  # 0.5 s step: no integration
    assert euler_from_quaternion(q_d)[2] == pytest.approx(EAST)
    logic.on_position(EAST, True, 0.52)
    logic.attitude(turn, 0.52)
    logic.reset_yaw()
    logic.on_position(SOUTH, True, 0.54)
    q_d, _, _ = logic.attitude(turn, 0.54)  # starts again from the real heading
    assert wrap_angle(euler_from_quaternion(q_d)[2] - SOUTH) == pytest.approx(
        0.0, abs=1e-9
    )


def test_no_fresh_heading_no_attitude():
    logic = Px4Logic(15)
    assert logic.attitude(Setpoint(0, 5.0, 0, HOVER, True), 0.0) is None
    logic.on_position(NORTH, False, 0.0)  # not good for control
    assert logic.attitude(Setpoint(0, 5.0, 0, HOVER, True), 0.0) is None
    logic.on_position(math.nan, True, 0.0)
    assert logic.attitude(Setpoint(0, 5.0, 0, HOVER, True), 0.0) is None
    logic.on_position(NORTH, True, 0.0)
    assert (
        logic.attitude(Setpoint(0, 5.0, 0, HOVER, True), POSITION_TIMEOUT + 0.01)
        is None
    )


# ---------- drone state for the guard (F13) ----------
def test_drone_state_follows_px4():
    logic = fresh(mask=11)
    logic.on_landed(False, 0.0)
    assert logic.drone_state(0.1) == {
        "esp_state": "STREAMING",
        "override_active": True,
        "mask": 11,
        "airborne": True,
        "fc_link_ok": True,
        "armed": True,
        "rx_time": 0.1,
    }


def test_override_needs_armed_and_offboard():
    logic = fresh()
    logic.on_status(True, False, 0.0)
    assert logic.drone_state(0.0)["override_active"] is False
    logic.on_status(False, True, 0.0)
    assert logic.drone_state(0.0)["override_active"] is False
    logic.on_status("yes", True, 0.0)  # only a real True
    assert logic.drone_state(0.0)["armed"] is False


def test_no_state_while_px4_is_not_fresh():
    assert Px4Logic(15).drone_state(0.0) is None
    logic = fresh()
    assert logic.drone_state(POSITION_TIMEOUT + 0.01) is None  # position stale
    logic.on_position(NORTH, True, 1.0)
    assert logic.drone_state(STATUS_TIMEOUT + 0.01) is None  # status stale
    logic.on_status(True, True, 1.0)
    assert logic.drone_state(0.9) is None  # clock going backwards


def test_a_bad_heading_keeps_the_link():
    logic = fresh()
    logic.on_position(math.nan, True, 0.3)
    assert logic.drone_state(0.3) is not None
    assert logic.heading_fresh(0.3) is True  # the last good one, still fresh


def test_airborne_needs_a_fresh_not_landed():
    logic = fresh()
    assert logic.drone_state(0.0)["airborne"] is False  # no land detector yet
    logic.on_landed(False, 0.0)
    logic.on_status(True, True, LAND_TIMEOUT + 0.1)
    logic.on_position(NORTH, True, LAND_TIMEOUT + 0.1)
    assert logic.drone_state(LAND_TIMEOUT + 0.1)["airborne"] is False  # silent detector


# ---------- the simulated pilot (masks 8, 11; F2 as changed 2026-11-24) ----------
GROUND = Setpoint(roll=-3.0, pitch=2.0, yaw_rate=10.0, throttle=HOVER, active=True)


def pilot(mask, velocity=(0.0, 0.0, 0.0), now=0.0):
    p = SimPilot(mask, HOVER)
    p.on_velocity(*velocity, now)
    return p


def test_mask_15_changes_nothing_and_needs_no_velocity():
    p = SimPilot(15, HOVER)
    assert not p.needed()
    assert p.fly(GROUND, 0.0) is GROUND


def test_mask_11_holds_height_keeps_the_grounds_axes():
    climbing = pilot(11, velocity=(0.0, 0.0, 0.2)).fly(GROUND, 0.0)
    sinking = pilot(11, velocity=(0.0, 0.0, -0.2)).fly(GROUND, 0.0)
    assert climbing.throttle < HOVER < sinking.throttle
    for out in (climbing, sinking):
        assert (out.roll, out.pitch, out.yaw_rate) == (-3.0, 2.0, 10.0)
        assert out.active


def test_mask_11_still_is_hover_divided_by_the_tilt():
    # 0.7 / (cos 3 deg * cos 2 deg): the share of thrust that lifts
    out = pilot(11).fly(GROUND, 0.0)
    expected = HOVER / (math.cos(math.radians(3.0)) * math.cos(math.radians(2.0)))
    assert out.throttle == pytest.approx(expected)


def test_mask_8_also_holds_the_horizontal():
    # Drifting forward and left: nose up (pitch -) and right side down (roll +)
    out = pilot(8, velocity=(0.2, 0.2, 0.0)).fly(GROUND, 0.0)
    assert out.pitch < 0.0 and out.roll > 0.0
    assert out.yaw_rate == 10.0  # yaw stays the ground's
    still = pilot(8).fly(GROUND, 0.0)
    assert (still.roll, still.pitch) == (0.0, 0.0)


def test_limits():
    # Far beyond the limits: tilt within 15 deg, throttle within hover +- 0.15
    out = pilot(8, velocity=(50.0, -50.0, 50.0)).fly(GROUND, 0.0)
    assert abs(out.pitch) <= 15.0 + 1e-9 and abs(out.roll) <= 15.0 + 1e-9
    # Correction at its limit (-0.15), then divided by the tilt, within the band:
    # 0.55 / (cos 15 deg)^2 = 0.589
    tilt = math.cos(math.radians(15.0)) ** 2
    assert out.throttle == pytest.approx((HOVER - 0.15) / tilt)
    assert HOVER - 0.15 <= out.throttle <= HOVER + 0.15


def test_integrates_while_the_error_stays():
    p = pilot(11, velocity=(0.0, 0.0, -0.1))
    first = p.fly(GROUND, 0.0).throttle
    for i in range(1, 50):
        p.on_velocity(0.0, 0.0, -0.1, i * 0.02)
        later = p.fly(GROUND, i * 0.02).throttle
    assert later > first  # still sinking: the integrator adds throttle


def test_no_fresh_velocity_is_none():
    assert SimPilot(11, HOVER).fly(GROUND, 0.0) is None  # never one
    stale = pilot(11, now=0.0)
    assert stale.fly(GROUND, PILOT_VELOCITY_TIMEOUT + 0.01) is None


def test_reset_clears_the_integrators():
    p = pilot(11, velocity=(0.0, 0.0, -0.1))
    for i in range(50):
        p.on_velocity(0.0, 0.0, -0.1, i * 0.02)
        p.fly(GROUND, i * 0.02)
    p.reset()
    p.on_velocity(0.0, 0.0, 0.0, 1.0)
    out = p.fly(GROUND, 1.0)
    assert out.throttle == pytest.approx(
        HOVER / (math.cos(math.radians(3.0)) * math.cos(math.radians(2.0)))
    )


# ---------- the neutral hold point ----------
def nan_at(values):
    return [math.isnan(v) for v in values]


def feed(hold, ned=(1.0, 2.0, -1.75), heading=EAST, now=0.0, valid=True):
    north, east, down = ned
    hold.on_position(north, east, valid, down, valid, heading, valid, now)


def test_hold_follows_the_drone_until_offboard():
    hold = NeutralHold()
    feed(hold)
    assert hold.target(False, 0.0) == approx((1.0, 2.0, -1.75, EAST))
    feed(hold, ned=(1.0, 2.0, -1.0))
    assert hold.target(False, 0.0) == approx((1.0, 2.0, -1.0, EAST))


def test_hold_freezes_on_the_first_offboard_tick():
    hold = NeutralHold()
    feed(hold)
    assert hold.target(True, 0.0) == approx((1.0, 2.0, -1.75, EAST))
    feed(hold, ned=(1.5, 2.0, -2.2), heading=NORTH, now=0.1)
    assert hold.target(True, 0.1) == approx((1.0, 2.0, -1.75, EAST))


def test_release_freezes_a_new_point():
    hold = NeutralHold()
    feed(hold)
    hold.target(True, 0.0)
    hold.release()
    feed(hold, ned=(3.0, 4.0, -1.0), now=0.1)
    assert hold.target(True, 0.1) == approx((3.0, 4.0, -1.0, EAST))


def test_leaving_offboard_unfreezes():
    hold = NeutralHold()
    feed(hold)
    hold.target(True, 0.0)
    feed(hold, ned=(0.0, 0.0, -1.0), now=0.1)
    assert hold.target(False, 0.1) == approx((0.0, 0.0, -1.0, EAST))
    assert hold.target(True, 0.1) == approx((0.0, 0.0, -1.0, EAST))


def test_hold_axes_need_a_fresh_value():
    hold = NeutralHold()
    assert nan_at(hold.target(True, 0.0)) == [True] * 4  # nothing yet
    hold = NeutralHold()
    feed(hold)
    late = HOLD_POSITION_TIMEOUT + 0.01
    assert nan_at(hold.target(True, late)) == [True] * 4


def test_hold_uses_each_axis_only_when_valid():
    # z valid, x/y not: a height hold only; a bad heading: no yaw hold
    hold = NeutralHold()
    hold.on_position(1.0, 2.0, False, -1.75, True, EAST, False, 0.0)
    assert nan_at(hold.target(True, 0.0)) == [True, True, False, True]
    assert hold.target(True, 0.0)[2] == -1.75


@pytest.mark.parametrize("bad", [math.nan, math.inf, None, True])
def test_hold_ignores_non_finite_values(bad):
    hold = NeutralHold()
    hold.on_position(bad, 2.0, True, bad, True, bad, True, 0.0)
    assert nan_at(hold.target(True, 0.0)) == [True] * 4
