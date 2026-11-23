"""Unit tests of guard.py and backends.py (no ROS)."""

import json
import math

import pytest

from flight_interface.backends import Backend, LoopbackBackend, make_backend
from flight_interface.guard import (
    LINK_TIMEOUT,
    MASK_AXES,
    MAX_TARGET_TIMEOUT,
    NEUTRAL,
    THROTTLE_BAND,
    TILT_CAP_DEG,
    YAW_RATE_CAP_DPS,
    Guard,
    Setpoint,
    limit_setpoint,
    parse_setpoint,
)

STREAMING = {
    "esp_state": "STREAMING",
    "override_active": True,
    "mask": 11,
    "airborne": True,
    "fc_link_ok": True,  # E6.1: control needs the FC link reported alive
}


HOVER = 0.7


def sp(roll=0.0, pitch=0.0, yaw_rate=0.0, throttle=HOVER):
    """An active (commanding) setpoint."""
    return Setpoint(roll, pitch, yaw_rate, throttle, True)


FORWARD = sp(pitch=5.0)  # nose down: forward (REP-103)


def ready(now=0.0, hover=HOVER):
    """Active, link up, no setpoint yet."""
    g = Guard(
        target_timeout=0.25, link_timeout=0.5, tick_period=0.02, hover_throttle=hover
    )
    g.activate()
    g.on_drone_state(STREAMING, now)
    return g


def as_json(**fields):
    base = {"roll": 0.0, "pitch": 5.0, "yaw_rate": 0.0, "throttle": HOVER}
    base["active"] = True
    base.update(fields)
    return json.dumps(base)


# ---------- parsing (F18) ----------
def test_parse_a_good_setpoint_and_ignore_extra_fields():
    assert parse_setpoint(as_json(reason="ok")) == Setpoint(0.0, 5.0, 0.0, HOVER, True)
    assert parse_setpoint(as_json(active=False)) == Setpoint(
        0.0, 5.0, 0.0, HOVER, False
    )
    assert parse_setpoint(as_json(roll=2)) == Setpoint(2.0, 5.0, 0.0, HOVER, True)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        None,
        "[1, 2]",
        json.dumps({"roll": 0, "pitch": 0, "yaw_rate": 0, "active": True}),
        as_json(pitch=None),
        as_json(roll=True),  # a bool is not a number
        as_json(yaw_rate="5"),
        as_json(active=1),  # only a real bool
        as_json(active="true"),
        as_json(throttle=1.2),
        as_json(throttle=-0.1),
    ],
)
def test_parse_drops_a_malformed_setpoint(text):
    assert parse_setpoint(text) is None


def test_parse_drops_non_finite():
    # json.dumps writes NaN / Infinity, which json.loads reads back.
    assert parse_setpoint(as_json(pitch=math.nan)) is None
    assert parse_setpoint(as_json(roll=math.inf)) is None


# ---------- limits (C1.4, F4, F14, F18) ----------
def test_limits_keep_a_setpoint_inside_them():
    limited, clamped = limit_setpoint(sp(-3.0, 4.0, 10.0, 0.75), 15, HOVER)
    assert limited == Setpoint(-3.0, 4.0, 10.0, 0.75, True)
    assert clamped == []


def test_limits_clamp_and_name_what_they_clamped():
    limited, clamped = limit_setpoint(sp(-20.0, 30.0, -90.0, 0.95), 15, HOVER)
    assert limited.roll == -TILT_CAP_DEG
    assert limited.pitch == TILT_CAP_DEG
    assert limited.yaw_rate == -YAW_RATE_CAP_DPS
    assert limited.throttle == pytest.approx(HOVER + THROTTLE_BAND)
    assert sorted(clamped) == ["pitch", "roll", "throttle", "yaw_rate"]
    low, _ = limit_setpoint(sp(throttle=0.1), 15, HOVER)
    assert low.throttle == pytest.approx(HOVER - THROTTLE_BAND)


def test_limits_inactive_is_neutral():
    assert limit_setpoint(Setpoint(20.0, 0, 0, 0.9, False), 15, HOVER) == (NEUTRAL, [])


def test_limits_no_hover_throttle_under_mask_15_is_neutral():
    # F16: throttle owned but nobody measured the hover value.
    assert limit_setpoint(FORWARD, 15, 0.0) == (NEUTRAL, [])
    # Under 8 and 11 the ground does not own throttle: still commanding.
    limited, _ = limit_setpoint(FORWARD, 11, 0.0)
    assert limited.active and limited.pitch == 5.0 and limited.throttle == 0.0


def test_hover_throttle_checked_at_construction():
    assert Guard().hover_throttle == 0.0
    assert Guard(hover_throttle=0.7).hover_throttle == 0.7
    for bad in (0.1, 0.9, -1.0, math.nan, 1.0):
        with pytest.raises(ValueError):
            Guard(hover_throttle=bad)


# ---------- construction ----------
def test_timeouts_never_above_the_requirement_and_bad_values_fall_back():
    assert Guard(target_timeout=5.0).target_timeout == MAX_TARGET_TIMEOUT
    assert Guard(target_timeout=math.nan).target_timeout == MAX_TARGET_TIMEOUT
    assert Guard(target_timeout=0.1).target_timeout == 0.1
    assert Guard(link_timeout=-1).link_timeout == LINK_TIMEOUT
    assert Guard(link_timeout=5.0).link_timeout == LINK_TIMEOUT  # watchdog stays on
    assert Guard(link_timeout=math.inf).link_timeout == LINK_TIMEOUT
    assert Guard(link_timeout=0.3).link_timeout == 0.3
    with pytest.raises(ValueError):
        Guard(tick_period=0)


# ---------- lifecycle: setpoints only while active ----------
def test_nothing_is_sent_while_inactive():
    g = Guard()
    g.on_drone_state(STREAMING, 0.0)
    g.on_setpoint(FORWARD, now=0.0)
    assert g.command(0.0) is None
    assert g.flight_state(0.0)["link_ok"] is False


def test_activate_needs_a_fresh_target_before_moving():
    g = Guard(hover_throttle=HOVER)
    g.on_drone_state(STREAMING, 0.0)
    g.on_setpoint(FORWARD, now=0.0)  # before activate: ignored
    g.activate()
    assert g.command(0.0) == NEUTRAL
    g.on_setpoint(FORWARD, now=0.01)
    assert g.command(0.01) == FORWARD


def test_deactivate_stops_and_drops_the_target():
    g = ready()
    g.on_setpoint(FORWARD, now=0.0)
    g.deactivate()
    assert g.command(0.0) is None
    g.activate()
    assert g.command(0.0) == NEUTRAL  # the old target is not replayed


# ---------- target freshness (REQ-SAF-017) ----------
def test_zero_goes_out_within_the_target_timeout_whatever_the_tick_phase():
    g = ready(1.0)
    g.on_setpoint(FORWARD, now=1.0)
    g.on_drone_state(STREAMING, 1.2)
    assert g.command(1.23) == FORWARD  # 0.23 + 0.02 <= 0.25
    assert g.command(1.24) == NEUTRAL


def test_clock_going_backwards_is_not_fresh():
    g = ready(1.0)
    g.on_setpoint(FORWARD, now=1.0)
    assert g.command(0.9) == NEUTRAL


# ---------- watchdog (REQ-SAF-012) ----------
def test_link_lost_gives_zero_and_link_ok_false():
    g = ready(0.0)
    g.on_setpoint(FORWARD, now=0.45)
    assert g.command(0.45) == FORWARD  # drone state 0.45 s old
    g.on_setpoint(FORWARD, now=0.6)
    assert g.command(0.6) == NEUTRAL  # drone state 0.6 s old > 0.5 s
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
    g.on_setpoint(sp(pitch=2.0), now=1.0)
    assert g.command(1.0) == sp(pitch=2.0)
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
        "armed": None,
        "failsafe_output": False,
        "fc_link_ok": True,
        "esp_rx_rejected": None,
        "active": True,
        "estop": False,
        "estop_reason": "",
        "resume_pending": False,
        "arm_pending": False,
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
    g.on_setpoint(FORWARD, now=0.0)
    g.trigger_estop("operator")
    assert g.command(0.0) is None
    g.on_setpoint(FORWARD, now=0.01)  # ignored while latched
    assert g.command(0.01) is None
    state = g.flight_state(0.01)
    assert (state["link_ok"], state["estop"], state["estop_reason"]) == (
        False,
        True,
        "operator",
    )


def test_estop_needs_an_explicit_reset_and_then_a_new_target():
    g = ready(0.0)
    g.on_setpoint(FORWARD, now=0.0)
    g.trigger_estop("")
    assert g.estop_reason == "estop"
    assert g.reset_estop() is True
    assert g.command(0.01) == NEUTRAL  # no replay of the old target
    g.on_setpoint(sp(pitch=1.0), now=0.02)
    assert g.command(0.02) == sp(pitch=1.0)
    assert g.reset_estop() is False  # nothing to reset


def test_estop_holds_across_deactivate_and_activate():
    g = ready(0.0)
    g.trigger_estop("operator")
    g.deactivate()
    g.activate()
    g.on_setpoint(FORWARD, now=0.0)
    assert g.command(0.0) is None


# ---------- REQ-SAF-023: never arms ----------
def test_guard_and_backends_have_no_way_to_arm_but_the_operator_path():
    # Decisions A4, K6: the backend's arm_control() is the one way to ARM,
    # called only for a request the operator made with ~/arm_control (checked
    # in the node tests); the guard only holds that request (request_arm,
    # arm_pending). Nothing else may exist.
    def arm_names(cls):
        return {n for n in dir(cls) if "arm" in n.lower().replace("alarm", "")}

    assert arm_names(Guard) == {"request_arm", "arm_pending"}
    assert arm_names(Backend) == {"arm_control"}
    assert arm_names(LoopbackBackend) == {"arm_control"}


# ---------- backends ----------
def test_loopback_is_conservative_by_default_and_records_what_is_sent():
    b = make_backend("loopback")
    assert b.poll(0.0) == [
        {
            "esp_state": "SESSION_IDLE",
            "override_active": False,
            "mask": None,
            "airborne": False,
            "fc_link_ok": True,
        }
    ]
    b.send_setpoint(FORWARD, 0.0)
    b.send_neutral(0.05)
    assert b.sent == [(0.0, FORWARD), (0.05, NEUTRAL)]
    b.connected = False
    assert b.poll(0.1) == []
    b.connected = True
    b.close()
    assert b.closed and b.poll(0.2) == []


def test_unknown_backend_refused():
    with pytest.raises(ValueError):
        make_backend("nope")


def test_base_backend_is_only_an_interface():
    b = Backend()
    with pytest.raises(NotImplementedError):
        b.send_setpoint(NEUTRAL, 0.0)
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
        {**STREAMING, "mask": 8.0},
        {**STREAMING, "mask": True},
        {"esp_state": "STREAMING"},
        {**STREAMING, "fc_link_ok": None},  # E6.1: unknown is not alive
        {**STREAMING, "fc_link_ok": False},
    ],
)
def test_zero_unless_the_drone_says_control_is_possible(drone):
    g = Guard()
    g.activate()
    g.on_drone_state(drone, 0.0)
    g.on_setpoint(sp(pitch=3.0, yaw_rate=10.0), now=0.0)
    assert g.command(0.0) == NEUTRAL
    assert g.control_possible(0.0) is False


def test_axes_outside_the_mask_are_level_and_hover():
    g = ready(0.0)  # mask 11: x, y, yaw
    g.on_setpoint(sp(-2.0, 3.0, 10.0, 0.8), now=0.0)
    assert g.command(0.0) == sp(-2.0, 3.0, 10.0, HOVER)
    g.on_drone_state({**STREAMING, "mask": 8}, 0.0)
    assert g.command(0.0) == sp(0.0, 0.0, 10.0, HOVER)
    g.on_drone_state({**STREAMING, "mask": 15}, 0.0)
    assert g.command(0.0) == sp(-2.0, 3.0, 10.0, 0.8)


def test_mask_before_limits_so_an_unowned_axis_is_never_reported_clamped():
    # Throttle 0.99 is beyond the band, but under mask 11 it is not ours.
    limited, clamped = limit_setpoint(sp(throttle=0.99), 11, HOVER)
    assert limited.throttle == HOVER and clamped == []
    # An unknown mask owns nothing.
    assert limit_setpoint(sp(20.0, 20.0, 90.0, 0.99), 99, HOVER) == (
        sp(0.0, 0.0, 0.0, HOVER),
        [],
    )


def test_command_names_what_it_clamped_and_clears_it_next_tick():
    g = ready(0.0)
    g.on_setpoint(sp(pitch=40.0), now=0.0)
    assert g.command(0.0).pitch == TILT_CAP_DEG
    assert g.clamped == ["pitch"]
    g.on_setpoint(FORWARD, now=0.01)
    g.command(0.01)
    assert g.clamped == []


def test_a_long_link_timeout_cannot_switch_the_watchdog_off():
    g = Guard(link_timeout=60.0)
    g.activate()
    g.on_drone_state(STREAMING, 0.0)
    g.on_setpoint(FORWARD, now=30.0)
    assert g.command(30.0) == NEUTRAL
    assert g.flight_state(30.0)["link_ok"] is False


def test_masks_and_limits_match_mission_node_and_the_controller():
    # Copies that must not drift apart: the masks (mission_node, this guard,
    # the controller) and the attitude caps (this guard, the controller, F4,
    # F14). The velocity ceilings are checked in controller_node's tests (F9).
    mission_logic = pytest.importorskip("mission_node.mission_logic")
    controller_logic = pytest.importorskip("controller_node.controller_logic")
    ours = {m: set(a) for m, a in MASK_AXES.items()}
    assert ours == {m: set(a) for m, a in mission_logic.MASK_AXES.items()}
    assert ours == {m: set(a) for m, a in controller_logic.MASK_AXES.items()}
    assert TILT_CAP_DEG == controller_logic.MAX_TILT_CAP_DEG
    assert THROTTLE_BAND == controller_logic.MAX_THROTTLE_BAND
    assert YAW_RATE_CAP_DPS == controller_logic.CEILING_YAW_DPS
