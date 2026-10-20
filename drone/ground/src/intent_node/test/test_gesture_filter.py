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
        self.send("INDEX_RIGHT", 5)
        self.assertEqual(self.intent(), "MOVE_RIGHT")

    def test_nothing_received_yet(self):
        self.assertEqual(self.intent(), "HOVER_LAND_REQUEST")
        self.assertEqual(self.reason(), "no_packets_yet")

    def test_needs_five_consecutive_packets(self):
        self.send("INDEX_RIGHT", 4)
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "unconfirmed")
        self.send("INDEX_RIGHT", 1)
        self.assertEqual(self.intent(), "MOVE_RIGHT")

    def test_one_noisy_packet_resets_the_count(self):
        self.send("INDEX_RIGHT", 4)
        self.send("CLOSED_HAND", 1)
        self.send("INDEX_RIGHT", 1)
        self.assertEqual(self.intent(), "NEUTRAL")

    # --- the bug found in v1: an old gesture was replayed after a gap ---
    def test_old_gesture_is_not_replayed_after_hand_gap(self):
        self.accept_right()
        self.f.update(packet(None))
        self.clock.advance(0.5)  # hand gone longer than 0.3 s
        self.f.update(packet("CLOSED_HAND"))  # first packet of a new gesture
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "unconfirmed")

    def test_old_gesture_is_not_replayed_even_if_same_gesture_returns(self):
        self.accept_right()
        self.clock.advance(0.5)
        self.f.update(packet("INDEX_RIGHT"))
        self.assertEqual(self.intent(), "NEUTRAL")

    def test_short_dropout_keeps_accepted_gesture(self):
        self.accept_right()
        self.f.update(packet(None))
        self.clock.advance(0.15)
        self.f.update(packet("INDEX_RIGHT"))
        self.assertEqual(self.intent(), "MOVE_RIGHT")

    def test_switching_gesture_needs_five_packets(self):
        self.accept_right()
        self.send("CLOSED_HAND", 2)
        self.assertEqual(self.intent(), "MOVE_RIGHT")  # still debouncing
        self.send("CLOSED_HAND", 3)
        self.assertEqual(self.intent(), "STOP_HOVER")

    def test_no_hand_goes_neutral_then_hover_land(self):
        self.accept_right()
        self.send(None, 6)  # sender keeps sending "no hand" for 0.6 s
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "no_hand")
        self.send(None, 20)  # ... and for 2 s more
        self.assertEqual(self.intent(), "HOVER_LAND_REQUEST")
        self.assertEqual(self.reason(), "no_hand_long")

    def test_silent_sender_goes_neutral_then_hover_land(self):
        self.accept_right()
        self.clock.advance(0.5)
        self.assertEqual(self.intent(), "NEUTRAL")
        self.clock.advance(2.0)
        self.assertEqual(self.intent(), "HOVER_LAND_REQUEST")
        self.assertEqual(self.reason(), "no_packets")

    def test_unknown_gesture_is_neutral(self):
        self.send("PEACE_SIGN", 6)
        self.assertEqual(self.intent(), "NEUTRAL")
        self.assertEqual(self.reason(), "unmapped_gesture")

    def test_every_vocabulary_gesture_maps(self):
        for gesture, expected in GESTURE_TO_INTENT.items():
            f = GestureFilter(clock=self.clock)
            for _ in range(5):
                f.update(packet(gesture))
            self.assertEqual(f.decide()["intent"], expected, gesture)

    def test_lowercase_and_spaces_are_accepted(self):
        self.send("  index_right ", 5)
        self.assertEqual(self.intent(), "MOVE_RIGHT")

    def test_bad_numbers_never_leak(self):
        self.f.update(
            {"gesture": "INDEX_RIGHT", "t": float("nan"), "xy": [1, float("inf")]}
        )
        out = self.f.decide()
        self.assertIsNone(out["t"])
        self.assertIsNone(out["xy"])

    def test_stale_timeout_never_shorter_than_neutral(self):
        f = GestureFilter(neutral_timeout=1.0, stale_timeout=0.2, clock=self.clock)
        self.assertEqual(f.stale_timeout, 1.0)


class HelperTests(unittest.TestCase):
    def test_normalize(self):
        self.assertIsNone(normalize_gesture(None))
        self.assertIsNone(normalize_gesture("none"))
        self.assertIsNone(normalize_gesture(" "))
        self.assertEqual(normalize_gesture("thumb_up"), "THUMB_UP")

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
