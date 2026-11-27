"""Unit tests for the cascade controller logic (#199). No ROS needed.

Run with:  python3 -m pytest test -q        (from the package folder)
"""

import json
import math
import unittest

from controller_node.controller_logic import (
    CEILING_LINEAR_MPS,
    CEILING_YAW_DPS,
    FLIGHT_STATE_TIMEOUT,
    GRAVITY,
    MAX_DT,
    MAX_INPUT_TIMEOUT,
    NO_HOVER_THROTTLE,
    NO_VELOCITY,
    OK,
    STALE_VELOCITY,
    Controller,
    Gains,
    Params,
    applied_from_state,
    clamp_target,
    tilted_throttle,
)

DT = 0.02  # 50 Hz, flight_interface's rate
HOVER = 0.7
BAND = 0.15
CAP = 15.0


# /flight/state while the output reaches the vehicle (flight_interface ACTIVE,
# STREAMING, override on): the integrators run.
APPLIED = {
    "link_ok": True,
    "esp_state": "STREAMING",
    "override_active": True,
    "mask": 15,
    "active": True,
    "estop": False,
}


def state(**changes):
    values = dict(APPLIED)
    values.update(changes)
    return json.dumps(values)


def make(mask=15, gains=None, **kwargs):
    if gains is None:
        gains = Gains()
    return Controller(Params(hover_throttle=HOVER, mask=mask, gains=gains, **kwargs))


def run(controller, target, measured, seconds, start=0.0, flight=None):
    """Feed the same target, measured velocity and /flight/state (applied by
    default) every tick; return the last output and the time after the run."""
    if flight is None:
        flight = state()
    now = start
    out = None
    ticks = round(seconds / DT)
    for _ in range(ticks):
        controller.on_target(*target, now)
        controller.on_velocity(*measured, now)
        controller.on_flight_state(flight, now)
        out = controller.step(now)
        now = now + DT
    return out, now


def one_step(target, measured=(0.0, 0.0, 0.0), mask=15, gains=None):
    controller = make(mask=mask, gains=gains)
    controller.on_target(*target, 0.0)
    controller.on_velocity(*measured, 0.0)
    return controller.step(0.0)


P_ONLY = Gains(kp_xy=1.0, ki_xy=0.0, kp_z=0.3, ki_z=0.0)


class TestClampTarget(unittest.TestCase):
    def test_inside_the_ceilings_unchanged(self):
        self.assertEqual(clamp_target(0.3, -0.2, 0.1, 0.2), (0.3, -0.2, 0.1, 0.2))

    def test_horizontal_scaled_keeping_direction(self):
        vx, vy, _, _ = clamp_target(0.6, 0.8, 0.0, 0.0)
        self.assertAlmostEqual(vx, 0.3)
        self.assertAlmostEqual(vy, 0.4)
        self.assertAlmostEqual(math.hypot(vx, vy), CEILING_LINEAR_MPS)

    def test_vertical_and_yaw_clamped_alone(self):
        _, _, vz, wz = clamp_target(0.0, 0.0, -3.0, 10.0)
        self.assertEqual(vz, -CEILING_LINEAR_MPS)
        self.assertAlmostEqual(math.degrees(wz), CEILING_YAW_DPS)

    def test_non_finite_is_zero(self):
        self.assertEqual(
            clamp_target(math.nan, math.inf, True, "1"), (0.0, 0.0, 0.0, 0.0)
        )


class TestSigns(unittest.TestCase):
    """REP-103 (ADR-025 C1.1 note): roll + right side down, pitch + nose
    down, yaw_rate + turn left."""

    def test_forward_is_nose_down(self):
        self.assertGreater(one_step((0.5, 0.0, 0.0, 0.0))["pitch"], 0.0)

    def test_backward_is_nose_up(self):
        self.assertLess(one_step((-0.5, 0.0, 0.0, 0.0))["pitch"], 0.0)

    def test_left_is_left_side_down(self):
        self.assertLess(one_step((0.0, 0.5, 0.0, 0.0))["roll"], 0.0)

    def test_right_is_right_side_down(self):
        self.assertGreater(one_step((0.0, -0.5, 0.0, 0.0))["roll"], 0.0)

    def test_up_is_more_throttle(self):
        self.assertGreater(one_step((0.0, 0.0, 0.5, 0.0))["throttle"], HOVER)

    def test_down_is_less_throttle(self):
        self.assertLess(one_step((0.0, 0.0, -0.5, 0.0))["throttle"], HOVER)

    def test_yaw_left_is_positive(self):
        out = one_step((0.0, 0.0, 0.0, math.radians(20.0)))
        self.assertAlmostEqual(out["yaw_rate"], 20.0)

    def test_moving_too_fast_forward_brakes(self):
        # Target 0, measured 0.4 m/s forward: tilt back (nose up).
        self.assertLess(one_step((0.0, 0.0, 0.0, 0.0), (0.4, 0.0, 0.0))["pitch"], 0.0)

    def test_worked_example(self):
        # The comment's example: 1 m/s of error, kp 1 -> 1 m/s^2 -> 5.8 deg.
        controller = make(gains=P_ONLY)
        controller.on_target(0.5, 0.0, 0.0, 0.0, 0.0)
        controller.on_velocity(-0.5, 0.0, 0.0, 0.0)
        pitch = controller.step(0.0)["pitch"]
        self.assertAlmostEqual(pitch, math.degrees(math.atan(1.0 / GRAVITY)))
        self.assertAlmostEqual(pitch, 5.8, places=1)


class TestLimits(unittest.TestCase):
    def test_tilt_never_above_the_cap(self):
        big = Gains(kp_xy=100.0, ki_xy=0.0, kp_z=0.3, ki_z=0.0)
        out = one_step((0.5, 0.5, 0.0, 0.0), (-5.0, -5.0, 0.0), gains=big)
        self.assertAlmostEqual(out["pitch"], CAP)
        self.assertAlmostEqual(out["roll"], -CAP)

    def test_throttle_inside_the_band(self):
        big = Gains(kp_xy=1.0, ki_xy=0.0, kp_z=100.0, ki_z=0.0)
        up = one_step((0.0, 0.0, 0.5, 0.0), (0.0, 0.0, -5.0), gains=big)
        down = one_step((0.0, 0.0, -0.5, 0.0), (0.0, 0.0, 5.0), gains=big)
        self.assertAlmostEqual(up["throttle"], HOVER + BAND)
        self.assertAlmostEqual(down["throttle"], HOVER - BAND)

    def test_tilt_compensation(self):
        # No vertical error, but tilted: more throttle than hover.
        out = one_step((0.5, 0.0, 0.0, 0.0), gains=P_ONLY)
        self.assertGreater(out["pitch"], 0.0)
        expected = HOVER / math.cos(math.radians(out["pitch"]))
        self.assertAlmostEqual(out["throttle"], expected)

    def test_target_clamped_before_use(self):
        fast = one_step((3.0, 0.0, 0.0, 0.0), gains=P_ONLY)
        ceiling = one_step((CEILING_LINEAR_MPS, 0.0, 0.0, 0.0), gains=P_ONLY)
        self.assertAlmostEqual(fast["pitch"], ceiling["pitch"])

    def test_yaw_clamped(self):
        out = one_step((0.0, 0.0, 0.0, math.radians(90.0)))
        self.assertAlmostEqual(out["yaw_rate"], CEILING_YAW_DPS)


class TestIntegrator(unittest.TestCase):
    def test_integral_removes_a_steady_error(self):
        # A constant 0.1 m/s shortfall: the output keeps growing past the
        # P-only value.
        controller = make()
        first, _ = run(controller, (0.1, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), DT)
        later, _ = run(controller, (0.1, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 2.0, DT)
        self.assertGreater(later["pitch"], first["pitch"])

    def test_anti_windup(self):
        # 10 s saturated forward, then the error flips: the output must leave
        # the cap at once, not after unwinding 10 s of integral.
        controller = make()
        out, now = run(controller, (0.5, 0.0, 0.0, 0.0), (-5.0, 0.0, 0.0), 10.0)
        self.assertAlmostEqual(out["pitch"], CAP)
        out, _ = run(controller, (0.0, 0.0, 0.0, 0.0), (0.5, 0.0, 0.0), 0.1, now)
        self.assertLess(out["pitch"], 0.0)

    def test_no_integration_on_a_long_step(self):
        controller = make(gains=Gains(kp_xy=0.0, ki_xy=1.0, kp_z=0.0, ki_z=0.0))
        controller.on_target(0.5, 0.0, 0.0, 0.0, 0.0)
        controller.on_velocity(0.0, 0.0, 0.0, 0.0)
        controller.on_flight_state(state(), 0.0)
        controller.step(0.0)
        later = 0.0 + MAX_DT + 0.05
        controller.on_target(0.5, 0.0, 0.0, 0.0, later)
        controller.on_velocity(0.0, 0.0, 0.0, later)
        controller.on_flight_state(state(), later)
        self.assertEqual(controller.step(later)["pitch"], 0.0)

    def test_first_step_does_not_integrate(self):
        out = one_step((0.5, 0.0, 0.0, 0.0), gains=Gains(0.0, 1.0, 0.0, 0.0))
        self.assertEqual(out["pitch"], 0.0)

    def test_cleared_when_inactive(self):
        controller = make(gains=Gains(kp_xy=0.0, ki_xy=1.0, kp_z=0.0, ki_z=0.0))
        out, now = run(controller, (0.5, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0)
        self.assertGreater(out["pitch"], 0.0)
        # No measured velocity for longer than the timeout: inactive.
        controller.step(now + 0.5)
        controller.on_target(0.5, 0.0, 0.0, 0.0, now + 0.5)
        controller.on_velocity(0.0, 0.0, 0.0, now + 0.5)
        controller.on_flight_state(state(), now + 0.5)
        self.assertEqual(controller.step(now + 0.5)["pitch"], 0.0)


class TestNotApplied(unittest.TestCase):
    """PR #253 review (Dot99 #1): no integration while the output does not
    reach the vehicle, so the first applied step has no wound-up term."""

    def test_pilot_flying_then_takeover_has_no_windup(self):
        # The pilot flies 0.3 m/s forward with override off for 120 s, target 0.
        controller = make()
        off = state(override_active=False)
        out, now = run(
            controller, (0.0, 0.0, 0.0, 0.0), (0.3, 0.0, 0.0), 120.0, flight=off
        )
        self.assertTrue(out["active"])  # F10: still active, only not applied
        self.assertEqual(controller._x.term, 0.0)
        # Takeover at zero velocity: no tilt from the past.
        out, _ = run(controller, (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), DT, now)
        self.assertEqual(out["pitch"], 0.0)

    def test_first_applied_step_has_no_old_term(self):
        # 2 s not applied with an error, then applied: kp * error plus only
        # this step's ki * error * dt.
        controller = make()
        error = 0.2
        off = state(esp_state="SESSION_IDLE")
        _, now = run(
            controller, (error, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 2.0, flight=off
        )
        out, _ = run(controller, (error, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), DT, now)
        gains = Gains()
        accel = gains.kp_xy * error + gains.ki_xy * error * DT
        expected = math.degrees(math.atan(accel / GRAVITY))
        self.assertAlmostEqual(out["pitch"], expected)

    def test_every_not_applied_state_clears(self):
        cases = {
            "link down / inactive / E-stop": state(link_ok=False),
            "not streaming": state(esp_state="LINK_FAILSAFE"),
            "override off": state(override_active=False),
            "no mask": state(mask=None),
            "unknown mask": state(mask=7),
            "not JSON": "not json",
            "a JSON list": "[1, 2]",
        }
        for name, flight in cases.items():
            with self.subTest(case=name):
                controller = make()
                run(controller, (0.3, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0)
                self.assertGreater(controller._x.term, 0.0)
                run(controller, (0.3, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), DT, 1.0, flight)
                self.assertEqual(controller._x.term, 0.0)

    def test_no_flight_state_yet_is_not_applied(self):
        controller = make()
        for tick in range(50):
            now = tick * DT
            controller.on_target(0.3, 0.0, 0.0, 0.0, now)
            controller.on_velocity(0.0, 0.0, 0.0, now)
            self.assertTrue(controller.step(now)["active"])
        self.assertEqual(controller._x.term, 0.0)

    def test_stale_flight_state_is_not_applied(self):
        controller = make()
        controller.on_flight_state(state(), 0.0)
        self.assertTrue(controller.output_applied(FLIGHT_STATE_TIMEOUT))
        self.assertFalse(controller.output_applied(FLIGHT_STATE_TIMEOUT + 0.01))

    def test_bad_state_text_is_reported(self):
        controller = make()
        self.assertTrue(controller.on_flight_state(state(), 0.0))
        self.assertFalse(controller.on_flight_state("{", 0.0))
        self.assertFalse(controller.on_flight_state(None, 0.0))

    def test_applied_from_state(self):
        self.assertTrue(applied_from_state(APPLIED))
        self.assertFalse(applied_from_state(None))
        self.assertFalse(applied_from_state(dict(APPLIED, mask=True)))
        self.assertFalse(applied_from_state(dict(APPLIED, override_active="yes")))


class TestThrottleWindup(unittest.TestCase):
    """Thiago (PR #253): the z anti-windup looks at the final throttle,
    after the tilt correction and the band."""

    def test_tilted_throttle_example(self):
        # The docstring's example: hover 0.70, band 0.15, correction 0.12
        throttle, high, low = tilted_throttle(
            0.12, 0.70, 0.15, math.cos(math.radians(10.0))
        )
        self.assertAlmostEqual(throttle, 0.82 / math.cos(math.radians(10.0)))
        self.assertFalse(high or low)
        throttle, high, _ = tilted_throttle(
            0.12, 0.70, 0.15, math.cos(math.radians(20.0))
        )
        self.assertAlmostEqual(throttle, 0.85)
        self.assertTrue(high)

    def test_no_windup_while_tilt_clamps_the_throttle(self):
        # Full tilt forward (15 deg, big horizontal error) and a climb error.
        # The top of the band (0.85) is reached once (0.70 + c) / cos(15 deg)
        # >= 0.85, i.e. c >= 0.85 * 0.966 - 0.70 = 0.121, while c is still
        # inside the band (0.15). The term must stop there, not at 0.15.
        gains = Gains(kp_xy=100.0, ki_xy=0.0, kp_z=0.05, ki_z=1.0)
        controller = make(gains=gains)
        out, now = run(controller, (0.5, 0.0, 0.2, 0.0), (-5.0, 0.0, 0.0), 3.0)
        self.assertAlmostEqual(out["throttle"], HOVER + BAND)
        stuck = controller._z.term
        run(controller, (0.5, 0.0, 0.2, 0.0), (-5.0, 0.0, 0.0), 5.0, now)
        self.assertEqual(controller._z.term, stuck)
        needed = (HOVER + BAND) * math.cos(math.radians(CAP)) - HOVER
        kp_part = gains.kp_z * 0.2
        step_max = gains.ki_z * 0.2 * DT  # one step may overshoot by this
        self.assertLessEqual(kp_part + stuck, needed + step_max)


class TestFreshness(unittest.TestCase):
    def test_no_measured_velocity_is_inactive(self):
        controller = make()
        controller.on_target(0.5, 0.0, 0.0, 0.0, 0.0)
        out = controller.step(0.0)
        self.assertFalse(out["active"])
        self.assertEqual(out, controller.neutral(NO_VELOCITY))
        self.assertEqual(out["throttle"], HOVER)

    def test_stale_measured_velocity_is_inactive(self):
        controller = make()
        controller.on_target(0.5, 0.0, 0.0, 0.0, 0.0)
        controller.on_velocity(0.0, 0.0, 0.0, 0.0)
        fresh = controller.step(MAX_INPUT_TIMEOUT)
        self.assertTrue(fresh["active"])
        self.assertEqual(fresh["reason"], OK)
        stale = controller.step(MAX_INPUT_TIMEOUT + 0.01)
        self.assertFalse(stale["active"])
        self.assertEqual(stale["reason"], STALE_VELOCITY)

    def test_non_finite_measured_velocity_is_dropped(self):
        controller = make()
        controller.on_velocity(0.0, 0.0, 0.0, 0.0)
        controller.on_velocity(math.nan, 0.0, 0.0, 0.2)
        self.assertFalse(controller.step(0.3)["active"])

    def test_measured_velocity_from_the_future_is_not_fresh(self):
        controller = make()
        controller.on_velocity(0.0, 0.0, 0.0, 1.0)
        self.assertFalse(controller.step(0.5)["active"])

    def test_stale_target_is_hover_and_still_active(self):
        controller = make(gains=P_ONLY)
        controller.on_target(0.5, 0.0, 0.0, 0.0, 0.0)
        now = MAX_INPUT_TIMEOUT + 0.01
        controller.on_velocity(0.0, 0.0, 0.0, now)
        out = controller.step(now)
        self.assertTrue(out["active"])
        self.assertEqual(out["pitch"], 0.0)
        self.assertEqual(out["throttle"], HOVER)

    def test_target_must_be_fresh_at_the_next_tick(self):
        # F11: target at 0.0, timeout 0.25 s, tick 0.02 s. At 0.23 the next
        # tick (0.25) is still inside: used. At 0.24 the next tick (0.26) is
        # not: zero now, so the zero is out by 0.24 s, never after 0.25 s.
        controller = make(gains=P_ONLY)
        controller.on_target(0.5, 0.0, 0.0, 0.0, 0.0)
        controller.on_velocity(0.0, 0.0, 0.0, 0.23)
        self.assertGreater(controller.step(0.23)["pitch"], 0.0)
        controller.on_velocity(0.0, 0.0, 0.0, 0.24)
        self.assertEqual(controller.step(0.24)["pitch"], 0.0)

    def test_tick_period_checked(self):
        for bad in (0.0, -0.02, 0.2, math.nan):
            with self.subTest(tick_period=bad):
                with self.assertRaises(ValueError):
                    Controller(Params(hover_throttle=HOVER, mask=15), tick_period=bad)

    def test_no_target_yet_is_hold(self):
        controller = make()
        controller.on_velocity(0.0, 0.0, 0.0, 0.0)
        out = controller.step(0.0)
        self.assertTrue(out["active"])
        self.assertEqual((out["roll"], out["pitch"], out["yaw_rate"]), (0.0, 0.0, 0.0))

    def test_zero_target_holds_against_drift(self):
        # F10: zero target, drifting left -> roll right (+) to stop.
        out = one_step((0.0, 0.0, 0.0, 0.0), (0.0, 0.3, 0.0))
        self.assertTrue(out["active"])
        self.assertGreater(out["roll"], 0.0)


class TestHoverNotMeasured(unittest.TestCase):
    """F16: hover_throttle 0.0 = not measured = always inactive."""

    def test_inactive_even_with_fresh_inputs(self):
        controller = Controller(Params(hover_throttle=0.0, mask=15))
        out, _ = run(controller, (0.5, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.5)
        self.assertFalse(out["active"])
        self.assertEqual(out["reason"], NO_HOVER_THROTTLE)
        self.assertEqual(controller._x.term, 0.0)

    def test_checked_before_the_velocity(self):
        controller = Controller(Params(hover_throttle=0.0, mask=8))
        self.assertEqual(controller.step(0.0)["reason"], NO_HOVER_THROTTLE)


class TestMask(unittest.TestCase):
    def test_mask_8_yaw_only(self):
        out = one_step((0.5, 0.5, 0.5, math.radians(20.0)), mask=8)
        self.assertEqual((out["roll"], out["pitch"]), (0.0, 0.0))
        self.assertEqual(out["throttle"], HOVER)
        self.assertAlmostEqual(out["yaw_rate"], 20.0)

    def test_mask_11_no_throttle(self):
        out = one_step((0.5, 0.5, 0.5, 0.0), mask=11)
        self.assertNotEqual(out["pitch"], 0.0)
        self.assertNotEqual(out["roll"], 0.0)
        self.assertEqual(out["throttle"], HOVER)

    def test_unowned_integrators_stay_zero(self):
        controller = make(mask=8)
        run(controller, (0.5, 0.5, 0.5, 0.0), (0.0, 0.0, 0.0), 1.0)
        self.assertEqual(controller._x.term, 0.0)
        self.assertEqual(controller._y.term, 0.0)
        self.assertEqual(controller._z.term, 0.0)


class TestParams(unittest.TestCase):
    def check_bad(self, **changes):
        values = {"hover_throttle": HOVER, "mask": 15}
        values.update(changes)
        with self.assertRaises(ValueError):
            Controller(Params(**values))

    def test_defaults_are_valid(self):
        Params(hover_throttle=HOVER, mask=15).validate()

    def test_bad_mask(self):
        for mask in (0, 7, 8.0, True, None):
            self.check_bad(mask=mask)

    def test_bad_hover(self):
        for hover in (1.0, -0.1, math.nan, None):
            self.check_bad(hover_throttle=hover)

    def test_band_must_fit_and_stay_small(self):
        self.check_bad(throttle_band=0.0)
        self.check_bad(throttle_band=0.2)
        self.check_bad(hover_throttle=0.1, throttle_band=0.15)
        self.check_bad(hover_throttle=0.9, throttle_band=0.15)

    def test_bad_tilt_cap(self):
        for cap in (0.0, 15.1, math.inf):
            self.check_bad(tilt_cap_deg=cap)

    def test_bad_timeouts(self):
        self.check_bad(target_timeout=0.0)
        self.check_bad(velocity_timeout=0.3)

    def test_bad_gains(self):
        self.check_bad(gains=Gains(kp_xy=-1.0))
        self.check_bad(gains=Gains(ki_z=math.nan))


class RetuneTests(unittest.TestCase):
    """Live tuning (Controller.retune): checked like at start-up, all or
    nothing, never the mask or the hover throttle."""

    def test_new_gain_changes_the_output(self):
        controller = make(gains=P_ONLY)
        controller.on_target(0.5, 0.0, 0.0, 0.0, 0.0)
        controller.on_velocity(0.0, 0.0, 0.0, 0.0)
        before = controller.step(0.0)["pitch"]
        doubled = Gains(kp_xy=2.0, ki_xy=0.0, kp_z=0.3, ki_z=0.0)
        controller.retune(Params(hover_throttle=HOVER, mask=15, gains=doubled))
        after = controller.step(DT)["pitch"]
        self.assertGreater(after, before * 1.9)

    def test_integrators_are_kept(self):
        controller = make()
        run(controller, (0.0, 0.0, 0.2, 0.0), (0.0, 0.0, 0.0), 1.0)
        term = controller._z.term
        self.assertGreater(term, 0.0)
        controller.retune(Params(hover_throttle=HOVER, mask=15, gains=Gains(kp_z=0.4)))
        self.assertEqual(controller._z.term, term)

    def test_ki_from_0_does_not_jump(self):
        # Thiago B: ki 0 for 60 s with 0.1 m/s of error, then ki 0.2. With a
        # bare integral this gave 6.97 deg at zero error; now nothing.
        controller = make(gains=P_ONLY)
        _, now = run(controller, (0.1, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 60.0)
        self.assertEqual(controller._x.term, 0.0)
        controller.retune(Params(hover_throttle=HOVER, mask=15, gains=Gains()))
        out, _ = run(controller, (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), DT, now)
        self.assertEqual(out["pitch"], 0.0)

    def test_ki_change_keeps_the_output(self):
        # ki 0.2 -> 1.0: the next output (same error, no time passed for the
        # new ki to act on) moves only by this step's ki * error * dt.
        controller = make()
        _, now = run(controller, (0.1, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 5.0)
        term = controller._x.term
        self.assertGreater(term, 0.0)
        controller.retune(Params(hover_throttle=HOVER, mask=15, gains=Gains(ki_xy=1.0)))
        out, _ = run(controller, (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), DT, now)
        # zero error now: the output is the kept term alone
        self.assertAlmostEqual(out["pitch"], math.degrees(math.atan(term / GRAVITY)))

    def test_bad_value_changes_nothing(self):
        controller = make()
        old = controller.params
        for bad in (
            Params(hover_throttle=HOVER, mask=15, tilt_cap_deg=20.0),
            Params(hover_throttle=HOVER, mask=15, gains=Gains(kp_xy=-1.0)),
            Params(hover_throttle=HOVER, mask=15, velocity_timeout=0.5),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    controller.retune(bad)
                self.assertIs(controller.params, old)

    def test_mask_and_hover_throttle_need_a_restart(self):
        controller = make()
        with self.assertRaisesRegex(ValueError, "mask"):
            controller.retune(Params(hover_throttle=HOVER, mask=11))
        with self.assertRaisesRegex(ValueError, "hover_throttle"):
            controller.retune(Params(hover_throttle=0.6, mask=15))

    def test_hover_throttle_0_pauses_and_the_start_value_resumes(self):
        # F28 note 2026-11-26: 0.0 = not measured = inactive (flight_interface
        # sends neutral); back to the start value = active again
        controller = make()
        controller.on_velocity(0.0, 0.0, 0.0, 0.0)
        controller.retune(Params(hover_throttle=0.0, mask=15))
        out = controller.step(0.0)
        self.assertFalse(out["active"])
        self.assertEqual(out["reason"], NO_HOVER_THROTTLE)
        with self.assertRaisesRegex(ValueError, "hover_throttle"):
            controller.retune(Params(hover_throttle=0.6, mask=15))
        controller.retune(Params(hover_throttle=HOVER, mask=15))
        self.assertTrue(controller.step(0.02)["active"])

    def test_a_bad_value_in_a_batch_changes_nothing(self):
        # All or none: a good gain together with a bad cap is refused whole
        controller = make()
        old = controller.params
        batch = Params(
            hover_throttle=HOVER, mask=15, tilt_cap_deg=20.0, gains=Gains(kp_xy=1.5)
        )
        with self.assertRaises(ValueError):
            controller.retune(batch)
        self.assertIs(controller.params, old)
        self.assertEqual(controller.params.gains.kp_xy, 1.0)


if __name__ == "__main__":
    unittest.main()
