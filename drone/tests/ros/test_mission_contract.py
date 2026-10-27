"""Contract between intent_node and mission_node. No ROS needed.

mission_node reads what intent_node publishes on /gesture/intent. Here the
real GestureFilter (intent_node's logic) and the real MissionLogic run on one
fake clock, and the filter's output is passed on as the exact JSON
intent_node publishes. A rename or a changed reason on either side breaks
these tests instead of silently stopping the drone, or worse, not stopping it.

Also checks that mission_node's pretend intent publisher (fake_intent) speaks
the same format and gesture names as intent_node.

Run with:  python3 -m pytest drone/tests/ros -v        (from the repo root)
or:        python3 -m unittest discover -s drone/tests/ros -v
"""

import json
import unittest

import support  # noqa: F401  (puts the packages on sys.path)
from mission_node.fake_inputs import FAKE_GESTURE, flight_state_json, intent_json
from mission_node.mission_logic import KNOWN_INTENTS, MissionLogic

from intent_node.gesture_filter import (
    GESTURE_TO_INTENT,
    INTENT_NEUTRAL,
    GestureFilter,
)

DT = 0.05  # both nodes run at 20 Hz
FISTS = "L:CLOSED_HAND+R:CLOSED_HAND"  # lock / unlock, held for 1 s
SILENT = object()  # the recogniser sends nothing at all
FLIGHT_OK = dict(
    link_ok=True, esp_state="STREAMING", override_active=True, mask=11, airborne=True
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Chain:
    """Recogniser packets -> GestureFilter -> JSON -> MissionLogic, 20 Hz."""

    def __init__(self):
        self.clock = FakeClock()
        self.filter = GestureFilter(clock=self.clock)
        self.mission = MissionLogic(clock=self.clock)
        self.flight = dict(FLIGHT_OK)
        self.intent = None
        self.out = None

    def run(self, seconds, gesture=SILENT):
        """Show `gesture` (None: no hand) for `seconds`; returns ticks as
        (time, intent message, mission output)."""
        ticks = []
        for _ in range(round(seconds / DT)):
            if gesture is not SILENT:
                self.filter.update(
                    {"gesture": gesture, "t": self.clock.now, "xy": None}
                )
            self.intent = self.filter.decide()
            self.mission.on_intent(json.dumps(self.intent, separators=(",", ":")))
            self.mission.on_flight_state(flight_state_json(**self.flight))
            self.out = self.mission.tick()
            ticks.append((self.clock.now, self.intent, self.out))
            self.clock.now += DT
        return ticks

    def mode(self):
        return self.out["state"]["mode"]

    def vel(self):
        return self.out["velocity"]

    def move_right(self):
        self.run(0.5, None)
        self.run(0.5, "L:OPEN_HAND_UP+R:OPEN_HAND_UP")
        self.run(1.5, "L:INDEX_RIGHT+R:NONE")


class VocabularyTests(unittest.TestCase):
    def test_mission_knows_every_intent_intent_node_sends(self):
        sent = set(GESTURE_TO_INTENT.values()) | {INTENT_NEUTRAL}
        self.assertEqual(sent - KNOWN_INTENTS, set())

    def test_mission_expects_no_intent_that_intent_node_never_sends(self):
        sent = set(GESTURE_TO_INTENT.values()) | {INTENT_NEUTRAL}
        self.assertEqual(KNOWN_INTENTS - sent, set())

    def test_fake_intent_uses_intent_node_message_format(self):
        real = GestureFilter(clock=FakeClock()).decide()
        fake = json.loads(intent_json("MOVE_RIGHT", "ok", 1.0))
        self.assertEqual(set(fake), set(real))

    def test_fake_gestures_are_the_ones_intent_node_maps(self):
        for intent, gesture in FAKE_GESTURE.items():
            with self.subTest(intent=intent):
                self.assertEqual(GESTURE_TO_INTENT.get(gesture), intent)
        self.assertEqual(set(FAKE_GESTURE), set(GESTURE_TO_INTENT.values()))


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.chain = Chain()

    def test_start_up_is_only_neutral(self):
        """intent_node starts with NEUTRAL / no_packets_yet: "no recogniser
        yet", never a land request, even when airborne."""
        self.chain.run(3.0, SILENT)
        self.assertEqual(self.chain.intent["reason"], "no_packets_yet")
        self.assertEqual(self.chain.mode(), "WAITING")
        self.assertEqual(self.chain.out["state"]["intent"], "NEUTRAL")
        self.assertFalse(self.chain.out["state"]["land_request"])

    def test_hands_up_at_start_up_do_not_engage(self):
        """Everything started while the operator already holds both hands up:
        intent_node goes no_packets_yet -> unconfirmed -> ENGAGE, and none of
        that is a new gesture."""
        self.chain.run(1.0, SILENT)
        self.chain.run(2.0, "L:OPEN_HAND_UP+R:OPEN_HAND_UP")
        self.assertEqual(self.chain.intent["intent"], "ENGAGE")
        self.assertEqual(self.chain.mode(), "WAITING")
        self.chain.run(0.5, None)  # hands down
        self.chain.run(0.5, "L:OPEN_HAND_UP+R:OPEN_HAND_UP")
        self.assertEqual(self.chain.mode(), "ENGAGED")

    def test_open_hand_engages_and_index_moves(self):
        self.chain.run(0.5, None)
        self.chain.run(0.5, "L:OPEN_HAND_UP+R:OPEN_HAND_UP")
        self.assertEqual(self.chain.mode(), "ENGAGED")
        self.chain.run(1.5, "L:INDEX_RIGHT+R:NONE")
        self.assertAlmostEqual(self.chain.vel()[1], -0.3)

    def test_gesture_before_open_hand_never_moves(self):
        ticks = self.chain.run(2.0, "L:INDEX_RIGHT+R:NONE")
        self.assertEqual(self.chain.intent["intent"], "MOVE_RIGHT")
        self.assertTrue(all(out["velocity"] == [0.0] * 4 for _, _, out in ticks))

    def test_hand_lost_stops_and_stays_engaged(self):
        self.chain.move_right()
        ticks = self.chain.run(3.0, None)
        start = ticks[0][0]
        stopped = next(t for t, _, out in ticks if out["velocity"] == [0.0] * 4)
        # intent_node: NEUTRAL after 0.3 s without a hand, plus one tick each
        self.assertLessEqual(stopped - start, 0.3 + 2 * DT + 1e-9)
        self.assertEqual(self.chain.intent["reason"], "no_hand_long")
        self.assertEqual(self.chain.mode(), "ENGAGED")
        self.assertEqual(self.chain.vel(), [0.0] * 4)
        self.assertFalse(self.chain.out["state"]["land_request"])
        self.chain.run(1.5, "L:INDEX_RIGHT+R:NONE")  # hands back, no new engage
        self.assertAlmostEqual(self.chain.vel()[1], -0.3)

    def test_recogniser_stops_and_stays_engaged(self):
        self.chain.move_right()
        ticks = self.chain.run(3.0, SILENT)
        stopped = next(t for t, _, out in ticks if out["velocity"] == [0.0] * 4)
        self.assertLessEqual(stopped - ticks[0][0], 0.3 + 2 * DT + 1e-9)
        self.assertEqual(self.chain.intent["reason"], "no_packets")
        self.assertEqual(self.chain.mode(), "ENGAGED")
        self.assertEqual(self.chain.vel(), [0.0] * 4)
        self.assertFalse(self.chain.out["state"]["land_request"])

    def test_unmapped_gesture_stops_without_counting_as_unknown(self):
        self.chain.move_right()
        self.chain.run(0.5, "ROCK_ON")
        self.assertEqual(self.chain.intent["reason"], "unmapped_gesture")
        self.assertEqual(self.chain.vel(), [0.0] * 4)
        self.assertEqual(self.chain.mission.unknown_intents, 0)

    def test_both_fists_lock_after_1_s_and_new_fists_unlock(self):
        self.chain.move_right()
        self.chain.run(0.8, FISTS)  # 5 packets to confirm, then 0.6 s held
        self.assertEqual(self.chain.mode(), "ENGAGED")
        self.assertEqual(self.chain.vel(), [0.0] * 4)  # hovering meanwhile
        self.chain.run(0.6, FISTS)
        self.assertEqual(self.chain.mode(), "LOCKED")
        self.chain.run(2.0, FISTS)  # held: stays locked
        self.chain.run(1.0, "L:INDEX_RIGHT+R:NONE")  # ignored
        self.assertEqual(self.chain.mode(), "LOCKED")
        self.assertEqual(self.chain.vel(), [0.0] * 4)
        self.chain.run(0.5, None)  # hands down
        self.chain.run(1.4, FISTS)
        self.assertEqual(self.chain.mode(), "ENGAGED")
        self.assertEqual(self.chain.out["state"]["mode_reason"], "unlock")

    def test_fists_to_lock_takes_1_s_after_confirmation(self):
        self.chain.move_right()
        ticks = self.chain.run(2.0, FISTS)
        start = ticks[0][0]
        locked = next(t for t, _, out in ticks if out["state"]["mode"] == "LOCKED")
        # intent_node confirms on the 5th packet (0.2 s), then 1 s held
        self.assertGreaterEqual(locked - start, 1.2 - 1e-9)
        self.assertLessEqual(locked - start, 1.2 + DT + 1e-9)

    def test_a_single_fist_does_nothing(self):
        self.chain.move_right()
        for fist in ("L:NONE+R:CLOSED_HAND", "L:CLOSED_HAND+R:NONE"):
            self.chain.run(2.0, fist)
            self.assertEqual(self.chain.intent["reason"], "unmapped_gesture")
            self.assertEqual(self.chain.mode(), "ENGAGED")
            self.assertEqual(self.chain.vel(), [0.0] * 4)

    def test_short_dropout_of_the_fists_does_not_unlock(self):
        """intent_node keeps a gesture through a dropout of up to 0.3 s, so
        that is not "fists dropped, then shown again"."""
        self.chain.move_right()
        self.chain.run(1.5, FISTS)
        self.assertEqual(self.chain.mode(), "LOCKED")
        self.chain.run(0.2, None)
        self.chain.run(1.5, FISTS)
        self.assertEqual(self.chain.mode(), "LOCKED")

    def test_short_dropout_does_not_break_the_hold(self):
        self.chain.move_right()
        self.chain.run(0.7, FISTS)
        self.chain.run(0.2, None)
        self.chain.run(0.5, FISTS)  # 1.2 s after confirming, dropout included
        self.assertEqual(self.chain.mode(), "LOCKED")

    def test_every_gesture_moves_only_owned_axes_under_mask_11(self):
        for gesture, intent in GESTURE_TO_INTENT.items():
            if intent in ("LOCK_TOGGLE", "ENGAGE"):
                continue
            with self.subTest(gesture=gesture):
                chain = Chain()
                chain.run(0.5, None)
                chain.run(0.5, "L:OPEN_HAND_UP+R:OPEN_HAND_UP")
                chain.run(1.5, gesture)
                self.assertEqual(chain.out["state"]["intent"], intent)
                self.assertEqual(chain.vel()[2], 0.0)  # throttle is the pilot's


if __name__ == "__main__":
    unittest.main()
