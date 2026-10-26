"""Unit tests for the gesture filter. No ROS needed.

Run with:  python3 -m unittest discover -s test -v      (from the package folder)
or:        colcon test --packages-select intent_node   (uses pytest)
"""

import math
import unittest

from intent_node.gesture_filter import (
    GESTURE_TO_INTENT,
    GestureFilter,
    clean_number,
    clean_xy,
    normalize_gesture,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def packet(gesture, t=1.0, xy=(0.5, 0.5)):
    return {"gesture": gesture, "t": t, "xy": list(xy) if xy else None}


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.f = GestureFilter(
            stable_packets=5, neutral_timeout=0.3, stale_timeout=2.0, clock=self.clock
        )

    def send(self, gesture, n=1, dt=0.1):
        for _ in range(n):
            self.f.update(packet(gesture))
            self.clock.advance(dt)

    def intent(self):
        return self.f.decide()["intent"]

    def reason(self):
        return self.f.decide()["reason"]

    def accept_right(self):
        self.send("L:INDEX_RIGHT+R:NONE", 5)
        self.assertEqual(self.intent(), "MOVE_RIGHT")

    # REQ-SAF-019
    def test_nothing_received_yet(self):
        self.assertEqual(self.intent(), "HOVER_LAND_REQUEST")
        self.assertEqual(self.reason(), "no_packets_yet")

    # REQ-SAF-020
    def test_needs_five_consecutive_packets(self):
        self.send("L:INDEX_RIGHT+R:NONE", 4)
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "unconfirmed")
        self.send("L:INDEX_RIGHT+R:NONE", 1)
        self.assertEqual(self.intent(), "MOVE_RIGHT")

    def test_one_noisy_packet_resets_the_count(self):
        self.send("L:INDEX_RIGHT+R:NONE", 4)
        self.send("L:NONE+R:CLOSED_HAND", 1)
        self.send("L:INDEX_RIGHT+R:NONE", 1)
        self.assertEqual(self.intent(), "NEUTRAL")

    # --- the bug found in v1: an old gesture was replayed after a gap ---
    # REQ-SAF-019
    def test_old_gesture_is_not_replayed_after_hand_gap(self):
        self.accept_right()
        self.f.update(packet(None))
        self.clock.advance(0.5)  # hand gone longer than 0.3 s
        self.f.update(packet("L:NONE+R:CLOSED_HAND"))  # first packet of a new gesture
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "unconfirmed")

    # REQ-SAF-019
    def test_old_gesture_is_not_replayed_even_if_same_gesture_returns(self):
        self.accept_right()
        self.clock.advance(0.5)
        self.f.update(packet("L:INDEX_RIGHT+R:NONE"))
        self.assertEqual(self.intent(), "NEUTRAL")

    def test_short_dropout_keeps_accepted_gesture(self):
        self.accept_right()
        self.f.update(packet(None))
        self.clock.advance(0.15)
        self.f.update(packet("L:INDEX_RIGHT+R:NONE"))
        self.assertEqual(self.intent(), "MOVE_RIGHT")

    def test_switching_gesture_needs_five_packets(self):
        self.accept_right()
        self.send("L:NONE+R:CLOSED_HAND", 2)
        self.assertEqual(self.intent(), "MOVE_RIGHT")  # still debouncing
        self.send("L:NONE+R:CLOSED_HAND", 3)
        self.assertEqual(self.intent(), "STOP_OR_HOVER")

    # REQ-SAF-019
    def test_no_hand_goes_neutral_then_hover_land(self):
        self.accept_right()
        self.send(None, 6)  # sender keeps sending "no hand" for 0.6 s
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "no_hand")
        self.send(None, 20)  # ... and for 2 s more
        self.assertEqual(self.intent(), "HOVER_LAND_REQUEST")
        self.assertEqual(self.reason(), "no_hand_long")

    # REQ-SAF-019
    def test_silent_sender_goes_neutral_then_hover_land(self):
        self.accept_right()
        self.clock.advance(0.5)
        self.assertEqual(self.intent(), "NEUTRAL")
        self.clock.advance(2.0)
        self.assertEqual(self.intent(), "HOVER_LAND_REQUEST")
        self.assertEqual(self.reason(), "no_packets")

    # REQ-SAF-020
    def test_unknown_gesture_is_neutral(self):
        self.send("PEACE_SIGN", 6)
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "unmapped_gesture")

    # REQ-SAF-020: gestures never arm, disarm or touch the override (Safety Rules #5)
    def test_no_gesture_arms_disarms_or_overrides(self):
        for gesture, intent in GESTURE_TO_INTENT.items():
            words = set(intent.split("_"))
            self.assertFalse(words & {"ARM", "DISARM", "OVERRIDE"}, gesture)

    def test_every_vocabulary_gesture_maps(self):
        for gesture, expected in GESTURE_TO_INTENT.items():
            f = GestureFilter(clock=self.clock)
            for _ in range(5):
                f.update(packet(gesture))
            self.assertEqual(f.decide()["intent"], expected, gesture)

    def test_two_hand_names_outside_the_table_are_neutral(self):
        # Commands use one raised hand. A second raised hand, or a name
        # without sides, never gives another command.
        for gesture in (
            "L:INDEX_UP+R:INDEX_UP",
            "L:CLOSED_HAND+R:INDEX_RIGHT",
            "L:OPEN_HAND_UP+R:NONE",
            "INDEX_RIGHT",
        ):
            f = GestureFilter(clock=self.clock)
            for _ in range(5):
                f.update(packet(gesture))
            self.assertEqual(f.decide()["reason"], "unmapped_gesture", gesture)

    def test_lowering_one_hand_never_gives_a_motion(self):
        # #81: when one hand of a two-hand gesture drops (lowered or lost),
        # what's left must not map to a different motion command.
        motionless = {"ENGAGE_OR_HOLD", "STOP_OR_HOVER", None}
        for gesture in GESTURE_TO_INTENT:
            left, right = gesture.split("+")
            if "NONE" in (left[2:], right[2:]):
                continue  # one-hand gesture already
            for rest in (f"{left}+R:NONE", f"L:NONE+{right}"):
                self.assertIn(GESTURE_TO_INTENT.get(rest), motionless, rest)

    def test_lowercase_and_spaces_are_accepted(self):
        self.send("  l:index_right+r:none ", 5)
        self.assertEqual(self.intent(), "MOVE_RIGHT")

    def test_bad_numbers_never_leak(self):
        self.f.update(
            {
                "gesture": "L:INDEX_RIGHT+R:NONE",
                "t": float("nan"),
                "xy": [1, float("inf")],
            }
        )
        out = self.f.decide()
        self.assertIsNone(out["t"])
        self.assertIsNone(out["xy"])

    def test_stale_timeout_never_shorter_than_neutral(self):
        f = GestureFilter(neutral_timeout=1.0, stale_timeout=0.2, clock=self.clock)
        self.assertEqual(f.stale_timeout, 1.0)

    def test_out_of_range_settings_are_clamped(self):
        f = GestureFilter(
            stable_packets=0, neutral_timeout=-1.0, stale_timeout=-5.0, clock=self.clock
        )
        self.assertEqual(f.stable_packets, 1)
        self.assertEqual(f.neutral_timeout, 0.0)
        self.assertEqual(f.stale_timeout, 0.0)

    def test_stable_packets_1_accepts_at_once(self):
        f = GestureFilter(stable_packets=1, clock=self.clock)
        f.update(packet("L:NONE+R:INDEX_UP"))
        self.assertEqual(f.decide()["intent"], "CLIMB_UP")

    def test_dropout_exactly_at_neutral_timeout_keeps_gesture(self):
        f = GestureFilter(neutral_timeout=0.5, clock=self.clock)
        for _ in range(5):
            f.update(packet("L:INDEX_RIGHT+R:NONE"))
        self.clock.advance(0.5)  # "more than 0.5 s" means 0.5 s itself is fine
        self.assertEqual(f.decide()["intent"], "MOVE_RIGHT")
        self.clock.advance(0.01)
        self.assertEqual(f.decide()["intent"], "NEUTRAL")

    def test_packet_without_gesture_key_counts_as_no_hand(self):
        self.accept_right()
        for _ in range(6):
            self.f.update({"t": 1.0})
            self.clock.advance(0.1)
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "no_hand")

    def test_odd_gesture_values_are_unmapped_not_errors(self):
        for value in (
            5,
            2.5,
            True,
            ["L:INDEX_RIGHT+R:NONE"],
            {"name": "L:INDEX_RIGHT+R:NONE"},
        ):
            with self.subTest(value=value):
                f = GestureFilter(clock=self.clock)
                for _ in range(5):
                    f.update({"gesture": value})
                self.assertEqual(f.decide()["intent"], "NEUTRAL")

    def test_unmapped_gesture_is_reported_by_name(self):
        self.send("INDEX_PINKY_UP", 5)
        out = self.f.decide()
        self.assertEqual(out["reason"], "unmapped_gesture")
        self.assertEqual(out["gesture"], "INDEX_PINKY_UP")

    def test_t_and_xy_come_from_the_latest_packet(self):
        self.f.update({"gesture": "L:INDEX_RIGHT+R:NONE", "t": 5.0, "xy": [0.1, 0.2]})
        self.f.update({"gesture": "L:INDEX_RIGHT+R:NONE", "t": 6.0})
        out = self.f.decide()
        self.assertEqual(out["t"], 6.0)
        self.assertIsNone(out["xy"])

    def test_recogniser_starting_without_hand_after_idle_is_hover_land(self):
        # intent_node ran > stale_timeout before the first packet, and that
        # packet has no hand: the hand counts as gone since the node started
        self.clock.advance(5.0)
        self.f.update(packet(None))
        self.assertEqual(self.intent(), "HOVER_LAND_REQUEST")
        self.assertEqual(self.reason(), "no_hand_long")


class HelperTests(unittest.TestCase):
    def test_normalize(self):
        self.assertIsNone(normalize_gesture(None))
        self.assertIsNone(normalize_gesture("none"))
        self.assertIsNone(normalize_gesture(" "))
        self.assertEqual(normalize_gesture("l:none+r:index_up"), "L:NONE+R:INDEX_UP")

    def test_every_no_hand_word_is_no_hand(self):
        for word in ("", "none", "No_Hand", " unknown ", "NEUTRAL"):
            self.assertIsNone(normalize_gesture(word), word)

    def test_clean_number(self):
        self.assertEqual(clean_number(3), 3.0)
        self.assertIsNone(clean_number(True))
        self.assertIsNone(clean_number("3"))
        self.assertIsNone(clean_number(math.nan))

    def test_clean_xy(self):
        self.assertEqual(clean_xy([0.1, 0.2]), [0.1, 0.2])
        self.assertIsNone(clean_xy([0.1]))
        self.assertIsNone(clean_xy("ab"))
        self.assertIsNone(clean_xy(None))


if __name__ == "__main__":
    unittest.main()
