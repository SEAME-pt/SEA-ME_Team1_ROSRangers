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
    INTENT_HOVER_LAND,
    INTENT_NEUTRAL,
    GestureFilter,
)

DT = 0.05  # both nodes run at 20 Hz
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
        self.run(0.5, "OPEN_HAND_UP")
        self.run(1.5, "INDEX_RIGHT")


class VocabularyTests(unittest.TestCase):
    def test_mission_knows_every_intent_intent_node_sends(self):
        sent = set(GESTURE_TO_INTENT.values()) | {INTENT_NEUTRAL, INTENT_HOVER_LAND}
        self.assertEqual(sent - KNOWN_INTENTS, set())

    def test_mission_expects_no_intent_that_intent_node_never_sends(self):
        sent = set(GESTURE_TO_INTENT.values()) | {INTENT_NEUTRAL, INTENT_HOVER_LAND}
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

    def test_start_up_is_not_a_land_request(self):
        """intent_node starts with HOVER_LAND_REQUEST / no_packets_yet: for
        mission_node that is "no recogniser yet", even when airborne."""
        self.chain.run(3.0, SILENT)
        self.assertEqual(self.chain.intent["reason"], "no_packets_yet")
        self.assertEqual(self.chain.mode(), "READY")
        self.assertEqual(self.chain.out["state"]["intent"], "NEUTRAL")

    def test_open_hand_engages_and_index_moves(self):
        self.chain.run(0.5, None)
        self.chain.run(0.5, "OPEN_HAND_UP")
        self.assertEqual(self.chain.mode(), "ENGAGED")
        self.chain.run(1.5, "INDEX_RIGHT")
        self.assertAlmostEqual(self.chain.vel()[1], -0.3)

    def test_gesture_before_open_hand_never_moves(self):
        ticks = self.chain.run(2.0, "INDEX_RIGHT")
        self.assertEqual(self.chain.intent["intent"], "MOVE_RIGHT")
        self.assertTrue(all(out["velocity"] == [0.0] * 4 for _, _, out in ticks))

    def test_hand_lost_stops_then_requests_landing(self):
        self.chain.move_right()
        ticks = self.chain.run(3.0, None)
        start = ticks[0][0]
        stopped = next(t for t, _, out in ticks if out["velocity"] == [0.0] * 4)
        # intent_node: NEUTRAL after 0.3 s without a hand, plus one tick each
        self.assertLessEqual(stopped - start, 0.3 + 2 * DT + 1e-9)
        self.assertEqual(self.chain.mode(), "LAND_REQUEST")
        self.assertEqual(self.chain.out["state"]["mode_reason"], "no_hand_long")

    def test_recogniser_stops_requests_landing(self):
        self.chain.move_right()
        ticks = self.chain.run(3.0, SILENT)
        stopped = next(t for t, _, out in ticks if out["velocity"] == [0.0] * 4)
        self.assertLessEqual(stopped - ticks[0][0], 0.3 + 2 * DT + 1e-9)
        self.assertEqual(self.chain.mode(), "LAND_REQUEST")
        self.assertEqual(self.chain.out["state"]["mode_reason"], "no_packets")

    def test_unmapped_gesture_stops_without_counting_as_unknown(self):
        self.chain.move_right()
        self.chain.run(0.5, "ROCK_ON")
        self.assertEqual(self.chain.intent["reason"], "unmapped_gesture")
        self.assertEqual(self.chain.vel(), [0.0] * 4)
        self.assertEqual(self.chain.mission.unknown_intents, 0)

    def test_closed_hand_disengages(self):
        self.chain.move_right()
        self.chain.run(0.5, "CLOSED_HAND")
        self.assertEqual(self.chain.mode(), "READY")
        self.assertEqual(self.chain.vel(), [0.0] * 4)

    def test_every_gesture_moves_only_owned_axes_under_mask_11(self):
        for gesture, intent in GESTURE_TO_INTENT.items():
            if intent in ("STOP_HOVER", "HOLD_POSITION"):
                continue
            with self.subTest(gesture=gesture):
                chain = Chain()
                chain.run(0.5, None)
                chain.run(0.5, "OPEN_HAND_UP")
                chain.run(1.5, gesture)
                self.assertEqual(chain.out["state"]["intent"], intent)
                self.assertEqual(chain.vel()[2], 0.0)  # throttle is the pilot's


if __name__ == "__main__":
    unittest.main()
