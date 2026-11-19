"""Unit tests for the mission logic. No ROS needed.

Run with:  python3 -m unittest discover -s test -v      (from the package folder)
or:        colcon test --packages-select mission_node   (uses pytest)
"""

import json
import math
import random
import unittest

from mission_node.fake_inputs import (
    DEMO_SCRIPT,
    FAKE_GESTURE,
    flight_state_json,
    intent_json,
    script_duration,
    step_at,
)
from mission_node.mission_logic import (
    AXES,
    CEILING_LINEAR_MPS,
    CEILING_YAW_DPS,
    ENGAGED,
    IDLE,
    INTENT_DIRECTION,
    LOCKED,
    MASK_AXES,
    WAITING,
    MissionLogic,
    parse_flight_state,
    parse_intent,
    ramp,
)

DT = 0.05  # 20 Hz
HOLD = 1.0  # lock_hold: both fists for 1 s lock and unlock
YAW_20 = math.radians(20.0)
ZERO = [0.0, 0.0, 0.0, 0.0]
# intent_node reasons that describe the hands ("03 Intent Node" §4). Written
# out here instead of imported, so the tests check the rule, not a copy of it.
HAND_REASONS = {"ok", "no_hand", "no_hand_long", "unmapped_gesture"}
STATE_KEYS = {
    "mode",
    "mode_reason",
    "intent",
    "reason",
    "gesture",
    "mask",
    "airborne",
    "land_request",
    "limited",
    "intent_age_ms",
}
# Inputs that must never crash the node or move the drone
NASTY_TEXTS = [
    None,
    "",
    " ",
    "null",
    "5",
    '"MOVE_RIGHT"',
    "[]",
    "{}",
    "{not json",
    "\x00\x01",
    "NaN",
    "[" * 100000 + "]" * 100000,  # deeper than Python's recursion limit
    '{"a":' * 100000 + "1" + "}" * 100000,
    '{"intent": {"x": 1}}',
    '{"intent": ["MOVE_RIGHT"]}',
    '{"intent": "MOVE-RIGHT", "reason": 5, "gesture": 7}',
    '{"intent": NaN}',
    '{"mask": 1e999, "link_ok": true}',
]


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Base(unittest.TestCase):
    """Drives the logic like the node does: inputs, then a tick every 50 ms."""

    def setUp(self):
        self.clock = FakeClock()
        self.m = MissionLogic(clock=self.clock)
        self.flight = dict(
            link_ok=True,
            esp_state="STREAMING",
            override_active=True,
            mask=11,
            airborne=True,
        )
        self.out = None

    def run_for(self, seconds, intent="NEUTRAL", reason="ok", flight=True):
        """Publish `intent` (None = silence) and the flight state at 20 Hz."""
        for _ in range(max(1, round(seconds / DT))):
            if intent is not None:
                self.m.on_intent(intent_json(intent, reason))
            if flight:
                self.m.on_flight_state(flight_state_json(**self.flight))
            self.out = self.m.tick()
            self.clock.advance(DT)
        return self.out

    def mode(self):
        return self.out["state"]["mode"]

    def vel(self):
        return self.out["velocity"]

    def engage(self):
        self.run_for(0.2, "NEUTRAL")
        self.run_for(0.2, "ENGAGE")
        self.assertEqual(self.mode(), ENGAGED)

    def lock(self):
        self.engage()
        self.run_for(HOLD + 0.2, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), LOCKED)


class ModeTests(Base):
    def test_starts_idle_without_flight_state(self):
        self.run_for(0.2, "ENGAGE", flight=False)
        self.assertEqual(self.mode(), IDLE)
        self.assertEqual(self.out["state"]["mode_reason"], "no_flight_state")
        self.assertEqual(self.vel(), [0.0, 0.0, 0.0, 0.0])

    def test_waiting_when_override_on(self):
        self.run_for(0.2)
        self.assertEqual(self.mode(), WAITING)

    def test_idle_for_each_flight_blocker(self):
        cases = [
            ("link_ok", False, "link_lost"),
            ("esp_state", "FAILSAFE", "not_streaming"),
            ("override_active", False, "override_off"),
            ("mask", 7, "bad_mask"),
        ]
        for key, value, reason in cases:
            with self.subTest(key=key):
                self.setUp()
                self.flight[key] = value
                self.run_for(0.2, "ENGAGE")
                self.assertEqual(self.mode(), IDLE)
                self.assertEqual(self.out["state"]["mode_reason"], reason)

    def test_engage_only_after_engage(self):
        self.run_for(0.2, "NEUTRAL")
        self.run_for(1.0, "MOVE_RIGHT")  # a stray gesture never starts motion
        self.assertEqual(self.mode(), WAITING)
        self.assertEqual(self.vel(), [0.0, 0.0, 0.0, 0.0])
        self.run_for(0.1, "ENGAGE")
        self.assertEqual(self.mode(), ENGAGED)

    def test_hold_already_shown_before_override_does_not_engage(self):
        self.flight["override_active"] = False
        self.run_for(0.5, "ENGAGE")
        self.flight["override_active"] = True
        self.run_for(0.5, "ENGAGE")
        self.assertEqual(self.mode(), WAITING)  # must be shown again on purpose
        self.run_for(0.2, "NEUTRAL")
        self.run_for(0.1, "ENGAGE")
        self.assertEqual(self.mode(), ENGAGED)

    def test_delivery_gap_while_holding_does_not_engage(self):
        self.flight["override_active"] = False
        self.run_for(0.2, "NEUTRAL", "no_hand")
        self.run_for(0.2, "ENGAGE")  # shown while IDLE: does not count
        self.flight["override_active"] = True
        self.run_for(0.2, "ENGAGE")
        self.assertEqual(self.mode(), WAITING)
        self.run_for(0.4, None)  # delivery gap: synthetic NEUTRAL (intent_stale)
        self.assertEqual(self.out["state"]["reason"], "intent_stale")
        self.run_for(0.5, "ENGAGE")  # same hands, still held
        self.assertEqual(self.mode(), WAITING)
        self.run_for(0.2, "NEUTRAL", "no_hand")  # a real release from intent_node
        self.run_for(0.1, "ENGAGE")
        self.assertEqual(self.mode(), ENGAGED)

    def test_both_fists_held_lock(self):
        self.engage()
        self.run_for(HOLD + 0.1, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), LOCKED)

    def test_override_off_goes_idle_from_engaged(self):
        self.engage()
        self.run_for(1.0, "MOVE_RIGHT")
        self.flight["override_active"] = False
        self.run_for(DT, "MOVE_RIGHT")
        self.assertEqual(self.mode(), IDLE)
        self.assertEqual(self.vel(), [0.0, 0.0, 0.0, 0.0])

    def test_stale_flight_state_goes_idle(self):
        self.engage()
        self.run_for(0.6, "MOVE_RIGHT", flight=False)  # timeout 0.5 s
        self.assertEqual(self.mode(), IDLE)
        self.assertEqual(self.out["state"]["mode_reason"], "flight_state_stale")

    def test_neutral_keeps_engaged_but_gives_zero(self):
        self.engage()
        self.run_for(1.0, "MOVE_RIGHT")
        self.run_for(DT, "NEUTRAL", "no_hand")
        self.assertEqual(self.mode(), ENGAGED)
        self.assertEqual(self.vel(), [0.0, 0.0, 0.0, 0.0])

    def test_no_hands_for_a_long_time_stays_engaged_and_hovers(self):
        self.engage()
        self.run_for(1.0, "MOVE_RIGHT")
        self.run_for(2.0, "NEUTRAL", "no_hand")
        self.run_for(30.0, "NEUTRAL", "no_hand_long")
        self.assertEqual(self.mode(), ENGAGED)
        self.assertEqual(self.vel(), ZERO)
        self.assertFalse(self.out["state"]["land_request"])
        self.run_for(1.0, "MOVE_RIGHT")  # hands back: no new engage needed
        self.assertAlmostEqual(self.vel()[1], -0.3)


class InputTests(Base):
    # REQ-SAF-017 (TimingTests checks every phase between message and tick)
    def test_stale_intent_gives_zero_within_0_25_s(self):
        self.engage()
        self.run_for(2.0, "MOVE_RIGHT")
        self.assertAlmostEqual(self.vel()[1], -0.3)
        self.run_for(0.25, None)  # intent_node went silent
        self.assertEqual(self.vel(), [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(self.out["state"]["reason"], "intent_stale")

    def test_never_received_is_not_lost(self):
        self.run_for(5.0, None)  # intent_node not started yet, airborne
        self.assertEqual(self.mode(), WAITING)
        self.assertEqual(self.out["state"]["reason"], "no_intent_yet")

    def test_lost_intent_topic_hovers_and_stays_engaged(self):
        self.engage()
        self.run_for(1.0, "MOVE_RIGHT")
        self.run_for(10.0, None)
        self.assertEqual(self.out["state"]["reason"], "intent_topic_lost")
        self.assertEqual(self.mode(), ENGAGED)
        self.assertEqual(self.vel(), ZERO)
        self.assertFalse(self.out["state"]["land_request"])
        self.run_for(1.0, "MOVE_RIGHT")  # intent_node back: moves again
        self.assertAlmostEqual(self.vel()[1], -0.3)

    def test_unknown_intent_is_neutral(self):
        self.engage()
        self.run_for(1.0, "MOVE_RIGHT")
        self.run_for(DT, "FLY_TO_THE_MOON")
        self.assertEqual(self.vel(), [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(self.out["state"]["intent"], "NEUTRAL")
        self.assertEqual(self.m.unknown_intents, 1)

    def test_bad_json_is_neutral(self):
        self.engage()
        self.run_for(1.0, "MOVE_RIGHT")
        self.m.on_intent("{not json")
        self.m.on_flight_state(flight_state_json(**self.flight))
        out = self.m.tick()
        self.assertEqual(out["velocity"], [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(self.m.bad_json, 1)

    def test_parse_intent(self):
        self.assertEqual(
            parse_intent('{"intent":"move_left","reason":"ok"}')[0], "MOVE_LEFT"
        )
        self.assertEqual(parse_intent("[1,2]")[3], "bad_json")
        self.assertEqual(parse_intent('{"intent":5}')[3], "unknown_intent")
        self.assertEqual(parse_intent(None)[3], "bad_json")

    def test_parse_flight_state_is_strict(self):
        state = parse_flight_state(
            json.dumps({"link_ok": "true", "override_active": 1, "mask": True})
        )
        self.assertFalse(state["link_ok"])  # a string is not a boolean
        self.assertFalse(state["override_active"])
        self.assertIsNone(state["mask"])
        self.assertIsNone(parse_flight_state("nope"))


class NoLandRequestTests(Base):
    """Nothing asks for a landing any more: only the flight controller
    telemetry will (not built yet). land_request stays false."""

    def test_intent_node_start_up_is_only_neutral(self):
        self.run_for(1.0, "NEUTRAL", "no_packets_yet")  # airborne
        self.assertEqual(self.mode(), WAITING)
        self.assertEqual(self.out["state"]["intent"], "NEUTRAL")
        self.assertFalse(self.out["state"]["land_request"])

    def test_dead_recogniser_hovers_and_stays_engaged(self):
        self.flight["mask"] = 15
        self.engage()
        self.run_for(1.0, "CLIMB_UP")
        self.run_for(10.0, "NEUTRAL", "no_packets")
        self.assertEqual(self.mode(), ENGAGED)
        self.assertEqual(self.vel(), ZERO)  # no descent either
        self.assertFalse(self.out["state"]["land_request"])

    def test_old_hover_land_request_intent_is_unknown(self):
        """An old intent_node that still sends it: NEUTRAL, never a landing."""
        self.engage()
        self.run_for(1.0, "MOVE_RIGHT")
        self.run_for(1.0, "HOVER_LAND_REQUEST", "no_hand_long")
        self.assertEqual(self.mode(), ENGAGED)
        self.assertEqual(self.vel(), ZERO)
        self.assertEqual(self.out["state"]["intent"], "NEUTRAL")
        self.assertEqual(self.out["state"]["reason"], "unknown_intent")
        self.assertFalse(self.out["state"]["land_request"])

    def test_on_the_ground_the_rules_are_the_same(self):
        self.flight["airborne"] = False
        self.engage()
        self.run_for(5.0, "NEUTRAL", "no_hand_long")
        self.assertEqual(self.mode(), ENGAGED)
        self.assertFalse(self.out["state"]["land_request"])


class LockTests(Base):
    def test_lock_needs_1_s_of_both_fists_and_hovers_meanwhile(self):
        self.flight["mask"] = 15
        self.engage()
        self.run_for(2.0, "CLIMB_UP")
        self.assertAlmostEqual(self.vel()[2], 0.2)
        self.run_for(DT, "LOCK_TOGGLE")  # held from t = 0
        self.assertEqual(self.vel(), ZERO)  # hovering at once
        self.run_for(HOLD - 0.15, "LOCK_TOGGLE")  # last tick at t = 0.85
        self.assertEqual(self.mode(), ENGAGED)
        self.run_for(0.25, "LOCK_TOGGLE")  # last tick at t = 1.1
        self.assertEqual(self.mode(), LOCKED)
        self.assertEqual(self.out["state"]["mode_reason"], "lock")
        self.assertEqual(self.vel(), ZERO)

    def test_hold_is_measured_from_the_first_fists_message(self):
        self.engage()
        self.run_for(0.5, "MOVE_RIGHT")
        ticks = 0
        while self.mode() == ENGAGED:
            self.run_for(DT, "LOCK_TOGGLE")
            ticks += 1
            self.assertLess(ticks, 40, "never locked")
        # the toggle tick is HOLD after the first one (float clock: +1 tick)
        self.assertIn(ticks, (round(HOLD / DT) + 1, round(HOLD / DT) + 2))

    def test_fists_dropped_before_1_s_do_not_lock(self):
        self.engage()
        for _ in range(3):
            self.run_for(HOLD - 0.2, "LOCK_TOGGLE")
            self.run_for(0.4, "NEUTRAL", "no_hand")
        self.assertEqual(self.mode(), ENGAGED)

    def test_another_gesture_restarts_the_hold(self):
        self.engage()
        self.run_for(HOLD - 0.2, "LOCK_TOGGLE")
        self.run_for(0.2, "MOVE_LEFT")
        self.run_for(HOLD - 0.2, "LOCK_TOGGLE")  # a new hold, not 1.4 s in total
        self.assertEqual(self.mode(), ENGAGED)
        self.run_for(0.3, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), LOCKED)

    def test_delivery_gap_breaks_the_hold(self):
        """A gap is NEUTRAL (intent_stale): the hold ends, and the same fists
        after the gap are no new gesture."""
        self.engage()
        self.run_for(HOLD - 0.2, "LOCK_TOGGLE")
        self.run_for(0.4, None)
        self.run_for(2 * HOLD, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), ENGAGED)

    def test_held_fists_toggle_only_once(self):
        self.lock()
        self.run_for(5.0, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), LOCKED)

    def test_every_other_intent_is_ignored_while_locked(self):
        for intent in INTENT_DIRECTION:
            if intent == "LOCK_TOGGLE":
                continue
            with self.subTest(intent=intent):
                self.setUp()
                self.flight["mask"] = 15
                self.lock()
                self.run_for(1.5, intent)
                self.assertEqual(self.mode(), LOCKED)
                self.assertEqual(self.vel(), ZERO)

    def test_locked_has_no_timeout(self):
        self.lock()
        self.run_for(30.0, "NEUTRAL", "no_hand_long")
        self.run_for(30.0, None)  # intent_topic_lost
        self.assertEqual(self.mode(), LOCKED)
        self.assertEqual(self.vel(), ZERO)
        self.assertFalse(self.out["state"]["land_request"])

    def test_unlock_needs_new_fists_held_1_s(self):
        self.lock()
        self.run_for(0.5, "NEUTRAL", "no_hand")
        self.run_for(HOLD - 0.15, "LOCK_TOGGLE")  # last tick at t = 0.8
        self.assertEqual(self.mode(), LOCKED)
        self.run_for(0.3, "LOCK_TOGGLE")  # last tick at t = 1.1
        self.assertEqual(self.mode(), ENGAGED)
        self.assertEqual(self.out["state"]["mode_reason"], "unlock")
        self.assertEqual(self.vel(), ZERO)  # the fists themselves are a hover
        self.run_for(1.0, "MOVE_RIGHT")
        self.assertAlmostEqual(self.vel()[1], -0.3)

    def test_fists_after_an_ignored_gesture_unlock(self):
        self.lock()
        self.run_for(0.5, "MOVE_LEFT")  # ignored, but the fists were dropped
        self.run_for(HOLD + 0.1, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), ENGAGED)

    def test_lock_unlock_lock(self):
        self.lock()
        for expected in (ENGAGED, LOCKED, ENGAGED):
            self.run_for(0.4, "NEUTRAL", "no_hand")
            self.run_for(HOLD + 0.1, "LOCK_TOGGLE")
            self.assertEqual(self.mode(), expected)

    def test_messages_that_say_nothing_about_the_hands_are_no_release(self):
        """Only intent_node's hand reasons can end held fists: not a delivery
        gap, bad JSON, intent_node (re)starting, a dead recogniser or a
        gesture still being confirmed."""
        for gap in (
            [(None, "")],
            [("NEUTRAL", "no_packets_yet")],
            [("NEUTRAL", "no_packets")],
            [("NEUTRAL", "unconfirmed")],
            [("BANANA", "ok")],
        ):
            with self.subTest(gap=gap):
                self.setUp()
                self.lock()
                for intent, reason in gap:
                    self.run_for(0.4, intent, reason)
                self.run_for(2 * HOLD, "LOCK_TOGGLE")
                self.assertEqual(self.mode(), LOCKED)

    def test_bad_json_is_no_release(self):
        self.lock()
        for _ in range(4):
            self.m.on_intent("{not json")
            self.m.on_flight_state(flight_state_json(**self.flight))
            self.m.tick()
            self.clock.advance(DT)
        self.run_for(2 * HOLD, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), LOCKED)

    def test_fists_while_waiting_do_nothing(self):
        self.run_for(0.2, "NEUTRAL", "no_hand")
        self.run_for(2 * HOLD, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), WAITING)

    def test_locked_goes_idle_then_waiting(self):
        self.lock()
        self.flight["override_active"] = False
        self.run_for(0.2, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), IDLE)
        self.flight["override_active"] = True
        self.run_for(0.2, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), WAITING)  # both open hands needed again
        self.run_for(0.2, "NEUTRAL", "no_hand")
        self.run_for(2 * HOLD, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), WAITING)

    def test_hold_cut_by_idle_does_not_lock_after_engaging_again(self):
        self.engage()
        self.run_for(HOLD - 0.2, "LOCK_TOGGLE")
        self.flight["link_ok"] = False
        self.run_for(0.2, "LOCK_TOGGLE")
        self.flight["link_ok"] = True
        self.run_for(0.4, "NEUTRAL", "no_hand")
        self.run_for(0.2, "ENGAGE")
        self.assertEqual(self.mode(), ENGAGED)
        self.run_for(HOLD - 0.2, "LOCK_TOGGLE")  # a new hold from zero
        self.assertEqual(self.mode(), ENGAGED)

    def test_lock_hold_parameter(self):
        for hold, ticks_to_lock in ((0.0, 1), (0.5, 11), (-1.0, 1), (float("nan"), 1)):
            with self.subTest(hold=hold):
                self.setUp()
                self.m = MissionLogic(lock_hold=hold, clock=self.clock)
                self.engage()
                self.run_for(0.2, "MOVE_RIGHT")
                ticks = 0
                while self.mode() == ENGAGED and ticks < 40:
                    self.run_for(DT, "LOCK_TOGGLE")
                    ticks += 1
                self.assertIn(ticks, (ticks_to_lock, ticks_to_lock + 1))
                self.assertEqual(self.mode(), LOCKED)

    def test_infinite_lock_hold_never_toggles(self):
        self.m = MissionLogic(lock_hold=float("inf"), clock=self.clock)
        self.engage()
        self.run_for(10.0, "LOCK_TOGGLE")
        self.assertEqual(self.mode(), ENGAGED)


class VelocityTests(Base):
    def test_intent_table(self):
        cases = {
            "MOVE_FORWARD": [0.3, 0.0, 0.0, 0.0],
            "MOVE_BACKWARD": [-0.3, 0.0, 0.0, 0.0],
            "MOVE_LEFT": [0.0, 0.3, 0.0, 0.0],
            "MOVE_RIGHT": [0.0, -0.3, 0.0, 0.0],
            "YAW_LEFT": [0.0, 0.0, 0.0, YAW_20],
            "YAW_RIGHT": [0.0, 0.0, 0.0, -YAW_20],
            "CLIMB_UP": [0.0, 0.0, 0.2, 0.0],
            "DESCEND_ONLY": [0.0, 0.0, -0.2, 0.0],
            "ENGAGE": [0.0, 0.0, 0.0, 0.0],
        }
        for intent, expected in cases.items():
            with self.subTest(intent=intent):
                self.setUp()
                self.flight["mask"] = 15
                self.engage()
                self.run_for(3.0, intent)
                for got, want in zip(self.vel(), expected):
                    self.assertAlmostEqual(got, want)

    def test_mask_11_zeroes_z(self):
        self.engage()
        self.run_for(2.0, "CLIMB_UP")
        self.assertEqual(self.vel()[2], 0.0)
        self.assertIn("axis_not_owned:z", self.out["state"]["limited"])

    def test_mask_8_only_yaw(self):
        self.flight["mask"] = 8
        self.engage()
        self.run_for(2.0, "MOVE_LEFT")
        self.assertEqual(self.vel()[1], 0.0)
        self.assertIn("axis_not_owned:y", self.out["state"]["limited"])
        self.run_for(2.0, "MOVE_FORWARD")
        self.assertEqual(self.vel()[0], 0.0)
        self.assertIn("axis_not_owned:x", self.out["state"]["limited"])
        self.run_for(2.0, "YAW_LEFT")
        self.assertAlmostEqual(self.vel()[3], YAW_20)

    def test_ramp_never_exceeds_max_accel(self):
        self.engage()
        previous = 0.0
        for _ in range(30):
            self.run_for(DT, "MOVE_RIGHT")
            step = abs(self.vel()[1] - previous)
            self.assertLessEqual(step, 0.5 * DT + 1e-9)
            previous = self.vel()[1]
        self.assertAlmostEqual(previous, -0.3)

    def test_stopping_is_immediate(self):
        self.engage()
        self.run_for(2.0, "MOVE_RIGHT")
        self.run_for(DT, "ENGAGE")  # both open hands while engaged: hover
        self.assertEqual(self.vel()[1], 0.0)
        self.assertEqual(self.mode(), ENGAGED)

    def test_reversing_goes_through_zero(self):
        self.engage()
        self.run_for(2.0, "MOVE_RIGHT")
        self.run_for(DT, "MOVE_LEFT")
        self.assertGreater(self.vel()[1], 0.0)
        self.assertLessEqual(self.vel()[1], 0.5 * DT + 1e-9)

    def test_ceiling_holds_even_with_high_parameters(self):
        self.m = MissionLogic(
            max_lateral_mps=5.0,
            max_vertical_mps=9.0,
            max_yaw_rate_dps=500.0,
            max_accel_mps2=100.0,
            max_yaw_accel_dps2=10000.0,
            clock=self.clock,
        )
        self.assertEqual(
            sorted(self.m.clamped_params),
            ["max_lateral_mps", "max_vertical_mps", "max_yaw_rate_dps"],
        )
        self.flight["mask"] = 15
        self.engage()
        self.run_for(1.0, "MOVE_LEFT")
        self.assertAlmostEqual(self.vel()[1], CEILING_LINEAR_MPS)
        self.run_for(1.0, "YAW_RIGHT")
        self.assertAlmostEqual(self.vel()[3], -math.radians(CEILING_YAW_DPS))

    def test_bad_parameters_give_zero_limits(self):
        m = MissionLogic(max_lateral_mps=float("nan"), max_vertical_mps=-1.0)
        self.assertEqual(m.max_lateral, 0.0)
        self.assertEqual(m.max_vertical, 0.0)

    def test_ramp_function(self):
        self.assertEqual(ramp(0.0, 0.3, 0.1), 0.1)
        self.assertEqual(ramp(0.3, 0.0, 0.1), 0.0)
        self.assertEqual(ramp(0.3, 0.1, 0.1), 0.1)
        self.assertEqual(ramp(-0.3, 0.3, 0.1), 0.1)

    def test_ramp_with_a_step_that_is_not_a_number_does_not_speed_up(self):
        nan = float("nan")
        self.assertEqual(ramp(0.0, -0.3, nan), 0.0)
        self.assertEqual(ramp(-0.1, -0.3, nan), -0.1)
        self.assertEqual(ramp(0.1, -0.3, nan), 0.0)  # through zero, then holds
        self.assertEqual(ramp(-0.3, -0.1, nan), -0.1)  # slowing down still works
        self.assertEqual(ramp(0.0, -0.3, float("inf")), -0.3)


class DemoScriptTests(Base):
    """Plays the fake_intent demo through the logic: the same sequence that
    `ros2 launch mission_node mission_demo.launch.py` shows."""

    def test_demo_sequence(self):
        modes = []
        elapsed = 0.0
        while True:
            step = step_at(elapsed)
            if step is None:
                break
            index, intent, reason = step
            self.run_for(DT, intent, reason)
            elapsed += DT
            if not modes or modes[-1][1] != self.mode():
                modes.append((index + 1, self.mode()))
        self.assertEqual(
            [mode for _, mode in modes],
            [WAITING, ENGAGED, LOCKED, ENGAGED],
        )
        self.assertEqual([index for index, _ in modes], [1, 2, 7, 9])
        self.assertEqual(len(DEMO_SCRIPT), 14)
        # Ends with intent_node silent: still engaged, hovering
        self.assertEqual(self.out["state"]["reason"], "intent_topic_lost")
        self.assertEqual(self.vel(), ZERO)


class TimingTests(Base):
    """REQ-SAF-017 measured from the last /gesture/intent message, whatever
    the phase between that message and the 20 Hz tick."""

    def zero_delay(self, offset):
        """Seconds from the last MOVE_RIGHT message (sent `offset` s before a
        tick) to the first zero target."""
        self.engage()
        self.run_for(2.0, "MOVE_RIGHT")
        self.assertAlmostEqual(self.vel()[1], -0.3)
        self.clock.advance(-offset)  # run_for left the clock at the next tick
        self.m.on_intent(intent_json("MOVE_RIGHT", "ok"))
        last = self.clock()
        self.clock.advance(offset)
        for _ in range(20):
            self.m.on_flight_state(flight_state_json(**self.flight))
            out = self.m.tick()
            if out["velocity"] == ZERO:
                self.assertEqual(out["state"]["reason"], "intent_stale")
                return self.clock() - last
            self.clock.advance(DT)
        self.fail("the target never went to zero")

    # REQ-SAF-017
    def test_zero_within_0_25_s_of_the_last_message(self):
        for offset in (0.001, 0.01, 0.025, 0.04, 0.049, DT):
            with self.subTest(offset=offset):
                self.setUp()
                self.assertLessEqual(self.zero_delay(offset), 0.25 + 1e-9)

    def test_one_or_two_missed_messages_do_not_stop(self):
        self.engage()
        self.run_for(2.0, "MOVE_RIGHT")
        self.run_for(0.15, None)  # three ticks without a message
        self.assertAlmostEqual(self.vel()[1], -0.3)
        self.assertEqual(self.out["state"]["reason"], "ok")

    def test_tick_period_cannot_make_the_watchdog_late(self):
        """A slower publish rate stops earlier, never later."""
        clock = FakeClock()
        m = MissionLogic(clock=clock, tick_period=0.2)
        m.on_intent(intent_json("MOVE_RIGHT", "ok"))
        clock.advance(0.06)
        self.assertEqual(m.tick()["state"]["reason"], "intent_stale")


class EngageTests(Base):
    def test_hold_shown_at_start_up_does_not_engage(self):
        """mission_node (re)started while the operator already holds both hands up."""
        self.run_for(1.0, "ENGAGE")
        self.assertEqual(self.mode(), WAITING)
        self.run_for(0.2, "NEUTRAL", "no_hand")
        self.run_for(0.1, "ENGAGE")
        self.assertEqual(self.mode(), ENGAGED)

    def test_hands_up_while_intent_node_starts_does_not_engage(self):
        """Everything started together: intent_node first says no_packets_yet,
        then confirms the hands that were already up."""
        self.run_for(1.0, "NEUTRAL", "no_packets_yet")
        self.run_for(0.2, "NEUTRAL", "unconfirmed")
        self.run_for(1.0, "ENGAGE")
        self.assertEqual(self.mode(), WAITING)
        self.run_for(0.4, "NEUTRAL", "no_hand")
        self.run_for(0.2, "NEUTRAL", "unconfirmed")
        self.run_for(0.2, "ENGAGE")
        self.assertEqual(self.mode(), ENGAGED)

    def test_back_from_idle_needs_a_new_engage(self):
        self.engage()
        self.run_for(1.0, "MOVE_RIGHT")
        self.flight["override_active"] = False
        self.run_for(0.2, "MOVE_RIGHT")
        self.flight["override_active"] = True
        self.run_for(1.0, "MOVE_RIGHT")
        self.assertEqual(self.mode(), WAITING)
        self.assertEqual(self.vel(), ZERO)

    def test_hold_kept_through_idle_does_not_re_engage(self):
        self.engage()
        self.flight["link_ok"] = False
        self.run_for(0.2, "ENGAGE")
        self.flight["link_ok"] = True
        self.run_for(0.5, "ENGAGE")
        self.assertEqual(self.mode(), WAITING)

    def test_unknown_intents_never_change_the_mode(self):
        self.engage()
        self.run_for(10.0, "BANANA")
        self.assertEqual(self.mode(), ENGAGED)
        self.assertEqual(self.vel(), ZERO)

    def test_unmapped_gesture_is_a_release(self):
        self.run_for(1.0, "ENGAGE")  # held at start-up
        self.run_for(0.4, "NEUTRAL", "unmapped_gesture")  # one hand lowered
        self.run_for(0.2, "ENGAGE")
        self.assertEqual(self.mode(), ENGAGED)

    def test_mode_reasons(self):
        self.run_for(0.2, "NEUTRAL")
        self.assertEqual(self.out["state"]["mode_reason"], "override_on")
        self.run_for(0.1, "ENGAGE")
        self.assertEqual(self.out["state"]["mode_reason"], "engage")
        self.run_for(HOLD + 0.1, "LOCK_TOGGLE")
        self.assertEqual(self.out["state"]["mode_reason"], "lock")
        self.run_for(0.1, "NEUTRAL", "no_hand")
        self.run_for(HOLD + 0.1, "LOCK_TOGGLE")
        self.assertEqual(self.out["state"]["mode_reason"], "unlock")


class MaskTests(Base):
    def test_mask_change_while_moving_zeroes_the_axis_at_once(self):
        self.engage()
        self.run_for(2.0, "MOVE_RIGHT")
        self.flight["mask"] = 8
        self.run_for(DT, "MOVE_RIGHT")
        self.assertEqual(self.vel(), ZERO)
        self.assertIn("axis_not_owned:y", self.out["state"]["limited"])

    def test_every_mask_and_intent(self):
        for mask, owned in MASK_AXES.items():
            for intent, direction in INTENT_DIRECTION.items():
                if intent == "LOCK_TOGGLE":
                    continue  # it leaves ENGAGED
                with self.subTest(mask=mask, intent=intent):
                    self.setUp()
                    self.flight["mask"] = mask
                    self.engage()
                    self.run_for(1.5, intent)
                    for i, axis in enumerate(AXES):
                        if axis not in owned or direction[i] == 0:
                            self.assertEqual(self.vel()[i], 0.0)
                        else:
                            self.assertNotEqual(self.vel()[i], 0.0)


class FlightStateTests(Base):
    def test_bad_flight_states_let_the_last_good_one_go_stale(self):
        self.engage()
        self.run_for(0.4, "MOVE_RIGHT", flight=False)
        for _ in range(4):
            self.m.on_flight_state("{")
        self.run_for(0.2, "MOVE_RIGHT", flight=False)
        self.assertEqual(self.mode(), IDLE)
        self.assertEqual(self.out["state"]["mode_reason"], "flight_state_stale")
        self.assertEqual(self.m.bad_flight_states, 4)

    def test_field_types_are_strict(self):
        cases = [
            ({"mask": 11.0}, "bad_mask"),
            ({"mask": "11"}, "bad_mask"),
            ({"mask": True}, "bad_mask"),
            ({"mask": None}, "bad_mask"),
            ({"link_ok": "true"}, "link_lost"),
            ({"link_ok": 1}, "link_lost"),
            ({"override_active": "yes"}, "override_off"),
            ({"esp_state": 5}, "not_streaming"),
            ({"esp_state": "FAILSAFE"}, "not_streaming"),
        ]
        for change, reason in cases:
            with self.subTest(change=change):
                self.setUp()
                state = dict(self.flight, **change)
                for _ in range(4):
                    self.m.on_intent(intent_json("ENGAGE", "ok"))
                    self.m.on_flight_state(json.dumps(state))
                    self.out = self.m.tick()
                    self.clock.advance(DT)
                self.assertEqual(self.mode(), IDLE)
                self.assertEqual(self.out["state"]["mode_reason"], reason)
                self.assertEqual(self.vel(), ZERO)

    def test_missing_fields_block(self):
        self.m.on_flight_state("{}")
        out = self.m.tick()
        self.assertEqual(out["state"]["mode"], IDLE)
        self.assertEqual(out["state"]["mode_reason"], "link_lost")

    def test_airborne_must_be_a_real_true(self):
        state = json.dumps(dict(self.flight, airborne="true"))
        for _ in range(4):
            self.m.on_intent(intent_json("NEUTRAL", "ok"))
            self.m.on_flight_state(state)  # not flight_state_json: it fixes types
            self.out = self.m.tick()
            self.clock.advance(DT)
        self.assertEqual(self.mode(), WAITING)
        self.assertFalse(self.out["state"]["airborne"])  # treated as on the ground

    def test_blocked_state_hides_mask_and_airborne(self):
        self.flight["override_active"] = False
        self.run_for(0.2)
        self.assertIsNone(self.out["state"]["mask"])
        self.assertFalse(self.out["state"]["airborne"])


class RobustnessTests(Base):
    def test_nasty_intents_give_zero_and_never_raise(self):
        for text in NASTY_TEXTS:
            with self.subTest(text=str(text)[:30]):
                self.setUp()
                self.engage()
                self.run_for(1.0, "MOVE_RIGHT")
                self.m.on_intent(text)
                self.m.on_flight_state(flight_state_json(**self.flight))
                out = self.m.tick()
                self.assertEqual(out["velocity"], ZERO)
                self.assertEqual(out["state"]["intent"], "NEUTRAL")

    def test_nasty_flight_states_are_ignored_and_never_raise(self):
        for text in NASTY_TEXTS:
            with self.subTest(text=str(text)[:30]):
                self.setUp()
                ok = self.m.on_flight_state(text)
                out = self.m.tick()
                self.assertEqual(out["state"]["mode"], IDLE)
                self.assertEqual(out["velocity"], ZERO)
                if not ok:
                    self.assertEqual(self.m.bad_flight_states, 1)

    def test_stalled_timer_does_not_jump_the_ramp(self):
        self.engage()
        self.run_for(DT, "MOVE_RIGHT")
        before = self.vel()[1]
        for _ in range(4):  # inputs keep coming, but no tick for 0.8 s
            self.clock.advance(0.2)
            self.m.on_intent(intent_json("MOVE_RIGHT", "ok"))
            self.m.on_flight_state(flight_state_json(**self.flight))
        out = self.m.tick()
        self.assertLessEqual(abs(out["velocity"][1] - before), 0.5 * 0.2 + 1e-9)

    def test_clock_going_backwards_does_not_move(self):
        self.engage()
        self.run_for(DT, "MOVE_RIGHT")
        before = self.vel()[1]
        self.clock.advance(-1.0)
        self.run_for(DT, "MOVE_RIGHT")
        self.assertLessEqual(abs(self.vel()[1]), abs(before) + 0.5 * DT + 1e-9)


class ParameterTests(unittest.TestCase):
    def test_intent_lost_is_never_before_the_timeout(self):
        m = MissionLogic(intent_timeout=1.0, intent_lost=0.5)
        self.assertEqual(m.intent_lost, 1.0)

    def test_nan_timeout_means_zero_at_once(self):
        clock = FakeClock()
        m = MissionLogic(intent_timeout=float("nan"), clock=clock)
        m.on_intent(intent_json("MOVE_RIGHT", "ok"))
        clock.advance(0.001)
        self.assertEqual(m.tick()["velocity"], ZERO)

    def test_negative_and_nan_accelerations_never_move(self):
        clock = FakeClock()
        m = MissionLogic(
            max_accel_mps2=-1.0, max_yaw_accel_dps2=float("nan"), clock=clock
        )
        self.assertEqual((m.max_accel, m.max_yaw_accel), (0.0, 0.0))

    def test_limit_that_is_not_a_number_is_zero(self):
        m = MissionLogic(max_lateral_mps="fast", max_yaw_rate_dps=None)
        self.assertEqual((m.max_lateral, m.max_yaw), (0.0, 0.0))
        self.assertEqual(m.clamped_params, [])

    def test_only_too_high_values_are_reported_as_clamped(self):
        m = MissionLogic(max_lateral_mps=0.5, max_yaw_rate_dps=45.0)
        self.assertEqual(m.clamped_params, [])
        m = MissionLogic(max_vertical_mps=0.51)
        self.assertEqual(m.clamped_params, ["max_vertical_mps"])


class InfiniteAccelerationTests(Base):
    """max_accel_mps2:=.inf ("no ramp"). A tick with dt 0 (two ticks at the
    same time, or the clock going backwards) gives inf * 0 = NaN as the step;
    the clamp turned that into +ceiling: full speed the WRONG way."""

    def setUp(self):
        super().setUp()
        inf = float("inf")
        self.m = MissionLogic(
            max_accel_mps2=inf, max_yaw_accel_dps2=inf, clock=self.clock
        )

    def tick_now(self, intent):
        """One tick at the same time as the previous one: dt 0."""
        self.clock.advance(-DT)
        return self.run_for(DT, intent)

    def test_jumps_to_the_target_at_once(self):
        self.engage()
        self.run_for(DT, "MOVE_RIGHT")
        self.assertEqual(self.vel(), [0.0, -0.3, 0.0, 0.0])

    def test_zero_dt_never_moves_the_wrong_way(self):
        for intent, axis in (("MOVE_RIGHT", 1), ("YAW_RIGHT", 3)):  # both negative
            with self.subTest(intent=intent):
                self.setUp()
                self.engage()
                velocity = self.tick_now(intent)["velocity"]
                self.assertFalse(any(math.isnan(v) for v in velocity), velocity)
                self.assertLessEqual(velocity[axis], 0.0, velocity)

    def test_clock_going_backwards_never_moves_the_wrong_way(self):
        self.engage()
        self.clock.advance(-1.0)
        self.run_for(DT, "MOVE_RIGHT")
        self.assertLessEqual(self.vel()[1], 0.0)


class YawRampTests(Base):
    def test_yaw_ramp_never_exceeds_max_yaw_accel(self):
        self.engage()
        previous = 0.0
        for _ in range(15):
            self.run_for(DT, "YAW_LEFT")
            self.assertLessEqual(
                self.vel()[3] - previous, math.radians(40.0) * DT + 1e-9
            )
            previous = self.vel()[3]
        self.assertAlmostEqual(previous, YAW_20)  # 0 -> 20 deg/s in 0.5 s

    def test_yaw_stops_at_once(self):
        self.engage()
        self.run_for(1.0, "YAW_RIGHT")
        self.run_for(DT, "NEUTRAL", "no_hand")
        self.assertEqual(self.vel()[3], 0.0)

    def test_ramping_is_reported(self):
        self.engage()
        self.run_for(DT, "MOVE_RIGHT")
        self.assertIn("ramping:y", self.out["state"]["limited"])
        self.run_for(1.0, "MOVE_RIGHT")
        self.assertNotIn("ramping:y", self.out["state"]["limited"])


class StateTests(Base):
    def test_state_has_every_documented_field(self):
        self.run_for(0.2)
        self.assertEqual(set(self.out["state"]), STATE_KEYS)
        json.dumps(self.out["state"])  # must be publishable as JSON

    def test_gesture_is_reported_only_after_the_first_intent(self):
        self.run_for(0.2, None)
        self.assertIsNone(self.out["state"]["gesture"])
        self.run_for(DT, "MOVE_RIGHT")
        self.assertEqual(self.out["state"]["gesture"], FAKE_GESTURE["MOVE_RIGHT"])

    def test_intent_age_is_reported(self):
        self.run_for(DT, "NEUTRAL")
        self.run_for(0.15, None)
        self.assertAlmostEqual(self.out["state"]["intent_age_ms"], 150, delta=1)


class InvariantTests(Base):
    """Random inputs for 20 minutes of simulated flight: the safety rules hold
    on every tick, whatever the sequence."""

    def test_random_inputs_keep_the_safety_rules(self):
        rng = random.Random(27)
        intents = sorted(INTENT_DIRECTION) + ["BANANA", "HOVER_LAND_REQUEST"]
        intents += ["ENGAGE", "LOCK_TOGGLE", None, None]  # more mode changes
        reasons = [
            "ok",
            "ok",
            "no_hand",
            "no_hand_long",
            "no_packets",
            "no_packets_yet",
            "unconfirmed",
            "unmapped_gesture",
        ]
        allowed = {
            (WAITING, ENGAGED): ("ENGAGE", "engage"),
            (ENGAGED, LOCKED): ("LOCK_TOGGLE", "lock"),
            (LOCKED, ENGAGED): ("LOCK_TOGGLE", "unlock"),
        }
        limits = (0.3, 0.3, 0.2, YAW_20)
        steps = (0.5 * DT, 0.5 * DT, 0.5 * DT, math.radians(40.0) * DT)
        previous, mode = ZERO, IDLE
        intent, reason, flight_on, last_hand = "NEUTRAL", "ok", True, None
        lock_start = None  # tick where a new lock hold started
        seen = set()
        for i in range(24000):
            if rng.random() < 0.05:  # a new intent every second on average
                intent, reason = rng.choice(intents), rng.choice(reasons)
            if rng.random() < 0.01:  # a new flight state every 5 s on average
                self.flight = dict(
                    link_ok=rng.random() < 0.9,
                    esp_state=rng.choice(["STREAMING"] * 5 + ["FAILSAFE"]),
                    override_active=rng.random() < 0.9,
                    mask=rng.choice([8, 11, 11, 15, 15, 7]),
                    airborne=rng.random() < 0.7,
                )
                flight_on = rng.random() < 0.9
            hand_before = last_hand
            if intent in INTENT_DIRECTION and reason in HAND_REASONS:
                last_hand = intent
            out = self.run_for(DT, intent, reason, flight=flight_on)
            v, s = out["velocity"], out["state"]
            where = f"tick {i}: {s}, velocity {v}"
            owned = MASK_AXES.get(s["mask"], set())
            for k, axis in enumerate(AXES):
                self.assertLessEqual(abs(v[k]), limits[k] + 1e-9, where)
                if axis not in owned:
                    self.assertEqual(v[k], 0.0, where)
                if v[k] * previous[k] < 0:
                    self.assertLessEqual(abs(v[k]), steps[k] + 1e-9, where)
                elif abs(v[k]) > abs(previous[k]):
                    self.assertLessEqual(
                        abs(v[k]) - abs(previous[k]), steps[k] + 1e-9, where
                    )
            self.assertFalse(s["land_request"], where)
            if s["mode"] != ENGAGED:
                self.assertEqual(v, ZERO, where)
            if s["reason"] in ("intent_stale", "no_intent_yet", "intent_topic_lost"):
                self.assertEqual(v, ZERO, where)
            if s["intent"] != "LOCK_TOGGLE":
                lock_start = None
            elif (
                intent == "LOCK_TOGGLE"
                and reason in HAND_REASONS
                and hand_before not in ("LOCK_TOGGLE", None)
            ):
                lock_start = i
            # A mode change other than to/from IDLE only happens on the
            # gesture that rules it: ENGAGE when it has just STARTED, the
            # lock gesture once held for HOLD s since it started
            before = WAITING if mode == IDLE else mode
            if s["mode"] not in (IDLE, before):
                gesture, mode_reason = allowed[(before, s["mode"])]
                self.assertEqual(
                    (s["intent"], s["mode_reason"]), (gesture, mode_reason), where
                )
                if gesture == "ENGAGE":
                    self.assertEqual(intent, "ENGAGE", where)
                    self.assertIn(reason, HAND_REASONS, where)
                    self.assertNotIn(hand_before, (gesture, None), where)
                else:
                    self.assertIsNotNone(lock_start, where)
                    self.assertGreaterEqual(i - lock_start, round(HOLD / DT), where)
                    lock_start = None  # one toggle per hold
                seen.add(s["mode"])
            previous, mode = v, s["mode"]
        self.assertEqual(seen, {ENGAGED, LOCKED})  # the run really changed modes


class FakeInputTests(unittest.TestCase):
    def test_demo_script_is_29_s(self):
        self.assertEqual(script_duration(), 29.0)

    def test_step_at_boundaries(self):
        self.assertEqual(step_at(0.0), (0, "NEUTRAL", "no_hand"))
        self.assertEqual(step_at(1.999)[0], 0)
        self.assertEqual(step_at(2.0)[0], 1)
        self.assertEqual(step_at(28.999)[0], 13)
        self.assertIsNone(step_at(29.0))

    def test_intent_json_format(self):
        data = json.loads(intent_json("MOVE_RIGHT", "ok", 12.5))
        self.assertEqual(
            data,
            {
                "intent": "MOVE_RIGHT",
                "gesture": FAKE_GESTURE["MOVE_RIGHT"],
                "reason": "ok",
                "t": 12.5,
                "xy": None,
            },
        )

    def test_flight_state_json_types(self):
        data = json.loads(flight_state_json(1, "STREAMING", 0, "15", 1))
        self.assertIs(data["link_ok"], True)
        self.assertIs(data["override_active"], False)
        self.assertEqual(data["mask"], 15)


if __name__ == "__main__":
    unittest.main()
