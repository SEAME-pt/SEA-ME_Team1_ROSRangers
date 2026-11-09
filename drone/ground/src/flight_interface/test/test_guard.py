"""Unit tests of guard.py and backends.py (no ROS)."""

import math

import pytest

from flight_interface.backends import Backend, LoopbackBackend, make_backend
from flight_interface.guard import (
    CEILING_LINEAR_MPS,
    CEILING_YAW_RPS,
    LINK_TIMEOUT,
    MAX_TARGET_TIMEOUT,
    ZERO,
    Guard,
    apply_mask,
    clamp_target,
)

STREAMING = {
    "esp_state": "STREAMING",
    "override_active": True,
    "mask": 11,
    "airborne": True,
}


def approx(values):
    return pytest.approx(values, abs=1e-9)


def ready(now=0.0):
    """Active, link up, no target yet."""
    g = Guard(target_timeout=0.25, link_timeout=0.5, tick_period=0.02)
    g.activate()
    g.on_drone_state(STREAMING, now)
    return g


# ---------- ceilings ----------
def test_clamp_keeps_targets_under_the_ceilings():
    assert clamp_target(0.3, -0.1, 0.2, 0.3) == approx((0.3, -0.1, 0.2, 0.3))


def test_clamp_horizontal_speed_as_a_whole_keeps_the_direction():
    vx, vy, vz, wz = clamp_target(2.0, -1.0, 0.0, 0.0)
    assert math.hypot(vx, vy) == pytest.approx(CEILING_LINEAR_MPS)
    assert vx / vy == pytest.approx(-2.0)


def test_clamp_vertical_and_yaw_on_their_own():
    assert clamp_target(0, 0, -3, 9) == approx(
        (0, 0, -CEILING_LINEAR_MPS, CEILING_YAW_RPS)
    )


def test_clamp_drops_non_finite_and_non_numbers():
    assert clamp_target(math.nan, math.inf, -math.inf, math.nan) == ZERO
    assert clamp_target(math.nan, 0.3, None, "x") == approx((0.0, 0.3, 0.0, 0.0))
    assert clamp_target(True, 0, 0, 0) == ZERO  # a bool is not a speed


# ---------- construction ----------
def test_timeouts_never_above_the_requirement_and_bad_values_fall_back():
    assert Guard(target_timeout=5.0).target_timeout == MAX_TARGET_TIMEOUT
    assert Guard(target_timeout=math.nan).target_timeout == MAX_TARGET_TIMEOUT
    assert Guard(target_timeout=0.1).target_timeout == 0.1
    assert Guard(link_timeout=-1).link_timeout == LINK_TIMEOUT
    with pytest.raises(ValueError):
        Guard(tick_period=0)


# ---------- lifecycle: setpoints only while active ----------
def test_nothing_is_sent_while_inactive():
    g = Guard()
    g.on_drone_state(STREAMING, 0.0)
    g.on_target(0.3, 0, 0, 0, now=0.0)
    assert g.command(0.0) is None
    assert g.flight_state(0.0)["link_ok"] is False


def test_activate_needs_a_fresh_target_before_moving():
    g = Guard()
    g.on_drone_state(STREAMING, 0.0)
    g.on_target(0.3, 0, 0, 0, now=0.0)  # before activate: ignored
    g.activate()
    assert g.command(0.0) == ZERO
    g.on_target(0.3, 0, 0, 0, now=0.01)
    assert g.command(0.01) == approx((0.3, 0, 0, 0))


def test_deactivate_stops_and_drops_the_target():
    g = ready()
    g.on_target(0.3, 0, 0, 0, now=0.0)
    g.deactivate()
    assert g.command(0.0) is None
    g.activate()
    assert g.command(0.0) == ZERO  # the old target is not replayed


# ---------- target freshness (REQ-SAF-017) ----------
def test_zero_goes_out_within_the_target_timeout_whatever_the_tick_phase():
    g = ready(1.0)
    g.on_target(0.3, 0, 0, 0, now=1.0)
    g.on_drone_state(STREAMING, 1.2)
    assert g.command(1.23) == approx((0.3, 0, 0, 0))  # 0.23 + 0.02 <= 0.25
    assert g.command(1.24) == ZERO


def test_clock_going_backwards_is_not_fresh():
    g = ready(1.0)
    g.on_target(0.3, 0, 0, 0, now=1.0)
    assert g.command(0.9) == ZERO


# ---------- watchdog (REQ-SAF-012) ----------
def test_link_lost_gives_zero_and_link_ok_false():
    g = ready(0.0)
    g.on_target(0.3, 0, 0, 0, now=0.45)
    assert g.command(0.45) == approx((0.3, 0, 0, 0))  # drone state 0.45 s old
    g.on_target(0.3, 0, 0, 0, now=0.6)
    assert g.command(0.6) == ZERO  # drone state 0.6 s old > 0.5 s
    state = g.flight_state(0.6)
    assert (
        state["link_ok"],
        state["esp_state"],
        state["override_active"],
        state["mask"],
    ) == (
        False,
        None,
        False,
        None,
    )


def test_link_comes_back_with_a_new_drone_state():
    g = ready(0.0)
    assert g.link_fresh(1.0) is False
    g.on_drone_state(STREAMING, 1.0)
    g.on_target(0.2, 0, 0, 0, now=1.0)
    assert g.command(1.0) == approx((0.2, 0, 0, 0))
    assert g.flight_state(1.0)["link_ok"] is True


def test_invalid_drone_states_do_not_feed_the_watchdog():
    g = Guard()
    g.activate()
    g.on_drone_state({"esp_state": "FLYING"}, 0.0)
    g.on_drone_state("STREAMING", 0.0)
    g.on_drone_state(None, 0.0)
    assert g.link_fresh(0.0) is False


def test_flight_state_follows_the_drone():
    g = ready(0.0)
    assert g.flight_state(0.1) == {
        "link_ok": True,
        "esp_state": "STREAMING",
        "override_active": True,
        "mask": 11,
        "airborne": True,
        "active": True,
        "estop": False,
        "estop_reason": "",
    }
    # Only a real True counts (mission_node parses strictly).
    g.on_drone_state(
        {"esp_state": "STREAMING", "override_active": "yes", "airborne": 1}, 0.2
    )
    state = g.flight_state(0.2)
    assert (state["override_active"], state["airborne"]) == (False, False)


# ---------- E-stop ----------
def test_estop_cuts_at_once_and_is_latched():
    g = ready(0.0)
    g.on_target(0.3, 0, 0, 0, now=0.0)
    g.trigger_estop("operator")
    assert g.command(0.0) is None
    g.on_target(0.3, 0, 0, 0, now=0.01)  # ignored while latched
    assert g.command(0.01) is None
    state = g.flight_state(0.01)
    assert (state["link_ok"], state["estop"], state["estop_reason"]) == (
        False,
        True,
        "operator",
    )


def test_estop_needs_an_explicit_reset_and_then_a_new_target():
    g = ready(0.0)
    g.on_target(0.3, 0, 0, 0, now=0.0)
    g.trigger_estop("")
    assert g.estop_reason == "estop"
    assert g.reset_estop() is True
    assert g.command(0.01) == ZERO  # no replay of the old target
    g.on_target(0.1, 0, 0, 0, now=0.02)
    assert g.command(0.02) == approx((0.1, 0, 0, 0))
    assert g.reset_estop() is False  # nothing to reset


def test_estop_holds_across_deactivate_and_activate():
    g = ready(0.0)
    g.trigger_estop("operator")
    g.deactivate()
    g.activate()
    g.on_target(0.3, 0, 0, 0, now=0.0)
    assert g.command(0.0) is None


# ---------- REQ-SAF-023: never arms ----------
def test_guard_and_backends_have_no_way_to_arm():
    names = {n.lower() for n in dir(Guard) + dir(Backend) + dir(LoopbackBackend)}
    assert not [n for n in names if "arm" in n.replace("alarm", "")]


# ---------- backends ----------
def test_loopback_is_conservative_by_default_and_records_what_is_sent():
    b = make_backend("loopback")
    assert b.poll(0.0) == [
        {
            "esp_state": "SESSION_IDLE",
            "override_active": False,
            "mask": None,
            "airborne": False,
        }
    ]
    b.send_setpoint([0.1, 0, 0, 0], 0.0)
    assert b.sent == [(0.0, (0.1, 0, 0, 0))]
    b.connected = False
    assert b.poll(0.1) == []
    b.connected = True
    b.close()
    assert b.closed and b.poll(0.2) == []


def test_unknown_backend_refused():
    with pytest.raises(ValueError):
        make_backend("esp")


def test_base_backend_is_only_an_interface():
    b = Backend()
    with pytest.raises(NotImplementedError):
        b.send_setpoint(ZERO, 0.0)
    with pytest.raises(NotImplementedError):
        b.poll(0.0)
    assert b.close() is None


# ---------- the drone must say control is possible; mask (REQ-SAF-018) ----------
@pytest.mark.parametrize(
    "drone",
    [
        {**STREAMING, "esp_state": "LINK_FAILSAFE"},
        {**STREAMING, "esp_state": "SESSION_IDLE"},
        {**STREAMING, "override_active": False},
        {**STREAMING, "override_active": "true"},
        {**STREAMING, "mask": 4},
        {**STREAMING, "mask": None},
        {"esp_state": "STREAMING"},
    ],
)
def test_zero_unless_the_drone_says_control_is_possible(drone):
    g = Guard()
    g.activate()
    g.on_drone_state(drone, 0.0)
    g.on_target(0.3, 0, 0, 0.1, now=0.0)
    assert g.command(0.0) == ZERO
    assert g.control_possible(0.0) is False


def test_axes_outside_the_mask_are_zero():
    g = ready(0.0)  # mask 11: x, y, yaw
    g.on_target(0.1, 0.2, 0.4, 0.3, now=0.0)
    assert g.command(0.0) == approx((0.1, 0.2, 0.0, 0.3))
    g.on_drone_state({**STREAMING, "mask": 8}, 0.0)
    assert g.command(0.0) == approx((0.0, 0.0, 0.0, 0.3))
    g.on_drone_state({**STREAMING, "mask": 15}, 0.0)
    assert g.command(0.0) == approx((0.1, 0.2, 0.4, 0.3))


def test_mask_before_ceilings_so_an_unowned_axis_never_slows_an_owned_one():
    assert apply_mask(0.4, 0.0, 5.0, 0.0, 11) == (0.4, 0.0, 0.0, 0.0)
    assert apply_mask(1, 1, 1, 1, 99) == ZERO  # unknown mask owns nothing
    g = ready(0.0)
    g.on_target(0.4, 0.0, 5.0, 0.0, now=0.0)
    assert g.command(0.0) == approx((0.4, 0.0, 0.0, 0.0))
