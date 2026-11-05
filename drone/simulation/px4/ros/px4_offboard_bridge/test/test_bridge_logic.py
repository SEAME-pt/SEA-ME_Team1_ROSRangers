"""Unit tests of bridge_logic (no ROS, no PX4)."""

import math

import pytest
from px4_offboard_bridge.bridge_logic import (
    CEILING_LINEAR_MPS,
    CEILING_YAW_RPS,
    ZERO,
    BridgeLogic,
    apply_mask,
    clamp_target,
    flu_body_to_ned,
)

NORTH, EAST, SOUTH, WEST = 0.0, math.pi / 2, math.pi, -math.pi / 2


def approx(values):
    return pytest.approx(values, abs=1e-9)


# ---------- FLU body -> NED ----------
@pytest.mark.parametrize(
    "yaw, body, ned",
    [
        # Facing north: forward = north, left = west (-east).
        (NORTH, (1, 0, 0, 0), (1, 0, 0, 0)),
        (NORTH, (0, 1, 0, 0), (0, -1, 0, 0)),
        # Facing east: forward = east, left = north.
        (EAST, (1, 0, 0, 0), (0, 1, 0, 0)),
        (EAST, (0, 1, 0, 0), (1, 0, 0, 0)),
        # Facing south: forward = south, left = east.
        (SOUTH, (1, 0, 0, 0), (-1, 0, 0, 0)),
        (SOUTH, (0, 1, 0, 0), (0, 1, 0, 0)),
        # Facing west: forward = west, left = south.
        (WEST, (1, 0, 0, 0), (0, -1, 0, 0)),
        (WEST, (0, 1, 0, 0), (-1, 0, 0, 0)),
    ],
)
def test_horizontal_axes_follow_the_heading(yaw, body, ned):
    assert flu_body_to_ned(*body, yaw) == approx(ned)


def test_up_is_minus_down_and_ccw_yaw_is_minus_yawspeed():
    assert flu_body_to_ned(0, 0, 0.2, 0, NORTH) == approx((0, 0, -0.2, 0))
    assert flu_body_to_ned(0, 0, 0, 0.3, EAST) == approx((0, 0, 0, -0.3))


def test_speed_is_kept_whatever_the_heading():
    for yaw in (0.3, 1.7, -2.4):
        n, e, _, _ = flu_body_to_ned(0.3, -0.2, 0, 0, yaw)
        assert math.hypot(n, e) == pytest.approx(math.hypot(0.3, 0.2))


# ---------- mask and clamp (REQ-SAF-018, REQ-CTL-001) ----------
def test_mask_zeroes_the_axes_the_ground_does_not_own():
    target = (0.1, 0.2, 0.3, 0.4)
    assert apply_mask(*target, 15) == target
    assert apply_mask(*target, 11) == (0.1, 0.2, 0.0, 0.4)
    assert apply_mask(*target, 8) == (0.0, 0.0, 0.0, 0.4)
    assert apply_mask(*target, 4) == ZERO  # unknown mask: owns nothing


def test_clamp_to_the_ceilings_and_drop_non_finite():
    assert clamp_target(2, -2, 0.1, 9) == (
        CEILING_LINEAR_MPS,
        -CEILING_LINEAR_MPS,
        0.1,
        CEILING_YAW_RPS,
    )
    assert clamp_target(math.nan, math.inf, -math.inf, math.nan) == ZERO


def test_unknown_mask_refused():
    with pytest.raises(ValueError):
        BridgeLogic(mask=4)


# ---------- freshness (REQ-SAF-017) ----------
def fresh_logic(mask=15):
    logic = BridgeLogic(mask=mask, target_timeout=0.25, tick_period=0.05)
    logic.on_position(NORTH, True, 0.0)
    logic.on_status(armed=True, offboard=True, now=0.0)
    return logic


def test_zero_before_any_target():
    assert fresh_logic().setpoint(0.1) == ZERO


def test_zero_goes_out_within_the_timeout_whatever_the_tick_phase():
    # Fresh only if still fresh at the next tick: the last non-zero is sent at an
    # age of at most 0.20 s, so the zero is out by 0.25 s (like mission_node).
    logic = fresh_logic()
    logic.on_position(NORTH, True, 1.0)
    logic.on_target(0.3, 0, 0, 0, now=1.0)
    assert logic.setpoint(1.20) == approx((0.3, 0, 0, 0))
    assert logic.setpoint(1.21) == ZERO
    assert logic.stale_targets == 1
    assert logic.setpoint(1.26) == ZERO
    assert logic.stale_targets == 2


def test_target_timeout_never_above_the_requirement():
    for bad in (5.0, 0.0, -1.0, math.nan, math.inf):
        assert BridgeLogic(target_timeout=bad).target_timeout == 0.25
    assert BridgeLogic(target_timeout=0.1).target_timeout == 0.1


def test_clock_going_backwards_is_not_fresh():
    logic = fresh_logic()
    logic.on_target(0.3, 0, 0, 0, now=10.0)
    assert logic.setpoint(9.0) == ZERO


def test_stale_heading_gives_zero_even_with_a_fresh_target():
    # A frozen heading would rotate the target the wrong way.
    logic = fresh_logic()  # heading at t = 0
    logic.on_target(0.3, 0, 0, 0, now=0.6)
    assert logic.setpoint(0.6) == ZERO


def test_heading_not_good_for_control_or_nan_is_ignored():
    logic = BridgeLogic()
    logic.on_target(0.3, 0, 0, 0, now=0.0)
    logic.on_position(EAST, False, 0.0)
    logic.on_position(math.nan, True, 0.0)
    assert logic.setpoint(0.0) == ZERO


def test_mask_11_never_commands_z():
    logic = fresh_logic(mask=11)
    logic.on_target(0, 0, 0.2, 0, now=0.0)
    assert logic.setpoint(0.0) == ZERO


def test_mask_8_only_yaw_through_the_logic():
    logic = fresh_logic(mask=8)
    logic.on_target(0.3, 0.3, 0.2, 0.3, now=0.0)
    assert logic.setpoint(0.0) == approx((0, 0, 0, -0.3))


def test_clamp_mask_and_rotate_together_at_any_heading():
    logic = BridgeLogic(mask=11)
    yaw = math.radians(30)
    logic.on_position(yaw, True, 0.0)
    logic.on_target(2.0, 0.0, 0.4, 9.0, now=0.0)  # over the ceilings, z not owned
    n, e, d, r = logic.setpoint(0.0)
    assert (n, e) == approx((0.5 * math.cos(yaw), 0.5 * math.sin(yaw)))
    assert (d, r) == approx((0.0, -CEILING_YAW_RPS))


# ---------- /flight/state ----------
def test_flight_state_follows_px4():
    logic = fresh_logic(mask=11)
    logic.on_landed(False)
    assert logic.flight_state(0.1) == {
        "link_ok": True,
        "esp_state": "STREAMING",
        "override_active": True,
        "mask": 11,
        "airborne": True,
    }
    logic.on_status(armed=True, offboard=False, now=0.2)  # pilot took over
    assert logic.flight_state(0.3)["override_active"] is False
    logic.on_status(armed=False, offboard=True, now=0.3)  # disarmed
    assert logic.flight_state(0.4)["override_active"] is False


def test_status_at_2_hz_keeps_the_link():
    # PX4 publishes vehicle_status every 500 ms: a 0.51 s gap is normal.
    logic = fresh_logic()
    logic.on_position(NORTH, True, 0.5)
    assert logic.flight_state(0.51)["link_ok"] is True


def test_link_lost_when_status_or_position_goes_quiet():
    logic = fresh_logic()
    logic.on_position(NORTH, True, 1.4)
    assert logic.flight_state(1.45)["link_ok"] is True
    state = logic.flight_state(1.6)  # status 1.6 s old > 1.5 s
    assert (state["link_ok"], state["esp_state"], state["override_active"]) == (
        False,
        "LINK_FAILSAFE",
        False,
    )
    logic = fresh_logic()
    logic.on_status(True, True, 0.6)
    assert logic.flight_state(0.6)["link_ok"] is False  # position 0.6 s old


def test_not_airborne_until_px4_says_so():
    assert BridgeLogic().flight_state(0.0)["airborne"] is False


def test_heading_not_yet_good_keeps_the_link_but_holds_zero():
    # First seconds after PX4 starts: messages arrive, heading not good for control.
    logic = BridgeLogic()
    logic.on_status(True, True, 0.0)
    logic.on_position(EAST, False, 0.0)
    logic.on_target(0.3, 0, 0, 0, now=0.0)
    assert logic.flight_state(0.0)["link_ok"] is True
    assert logic.setpoint(0.0) == ZERO
