"""Unit tests for the cascade controller logic (#199). No ROS needed.

Run with:  python3 -m pytest test -q        (from the package folder)
"""

import math
import unittest

from controller_node.controller_logic import (
    CEILING_LINEAR_MPS,
    CEILING_YAW_DPS,
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
    clamp_target,
)

DT = 0.02  # 50 Hz, flight_interface's rate
HOVER = 0.7
BAND = 0.15
CAP = 15.0


def make(mask=15, gains=None, **kwargs):
    if gains is None:
        gains = Gains()
    return Controller(Params(hover_throttle=HOVER, mask=mask, gains=gains, **kwargs))


def run(controller, target, measured, seconds, start=0.0):
    """Feed the same target and measured velocity every tick; return the last
    output and the time after the run."""
    now = start
    out = None
    ticks = round(seconds / DT)
    for _ in range(ticks):
        controller.on_target(*target, now)
        controller.on_velocity(*measured, now)
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
        controller.step(0.0)
        later = 0.0 + MAX_DT + 0.05
        controller.on_target(0.5, 0.0, 0.0, 0.0, later)
        controller.on_velocity(0.0, 0.0, 0.0, later)
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
        self.assertEqual(controller.step(now + 0.5)["pitch"], 0.0)


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
        self.assertEqual(controller._x.integral, 0.0)

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
        self.assertEqual(controller._x.integral, 0.0)
        self.assertEqual(controller._y.integral, 0.0)
        self.assertEqual(controller._z.integral, 0.0)


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


if __name__ == "__main__":
    unittest.main()
