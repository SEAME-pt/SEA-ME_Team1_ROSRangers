"""Unit tests for the gesture maths in hand_processing.py. No ROS or camera needed.

recognize() takes 21 (x, y) landmarks (pixels, y down) and returns a gesture
name. The hands below are built from known finger states and a known
direction, so each test says: "this hand shape must give this gesture".

MediaPipe is only needed to run the camera. If it is not installed, a stub is
used so these tests still run with the system Python.

Run with:  python3 -m unittest discover -s test -v      (from the package folder)
or:        colcon test --packages-select gesture_node   (uses pytest)
"""

import math
import sys
import types
import unittest

try:
    import mediapipe  # noqa: F401
except ImportError:
    _mp = types.ModuleType("mediapipe")
    _mp.solutions = types.SimpleNamespace(hands=None, drawing_utils=None)
    sys.modules["mediapipe"] = _mp

from gesture_node.hand_processing import (  # noqa: E402
    EXTENDED_ANGLE,
    INDEX,
    THUMB,
    angle,
    is_finger_extended,
    pointing_direction,
    recognize,
    thumb_out,
)

DIRECTIONS = {"UP": 0, "RIGHT": 90, "DOWN": 180, "LEFT": -90}


def make_hand(extended=(), direction="UP", scale=200.0, origin=(320.0, 240.0)):
    """21 landmarks of a hand with the given fingers extended.

    Built pointing up with the wrist at (0, 0) and hand size 1 (wrist to
    middle knuckle), then rotated so the extended fingers point to
    `direction`, scaled to pixels and moved to `origin`.
    """
    lm = [(0.0, 0.0)] * 21
    # Thumb: 1 CMC, 2 MCP, 3 IP, 4 tip. Out = tip well past the index knuckle.
    lm[1] = (0.25, -0.2)
    lm[2] = (0.4, -0.4)
    lm[3] = (0.6, -0.55)
    lm[4] = (0.9, -0.7) if "THUMB" in extended else (0.2, -0.6)
    # Other fingers: MCP, PIP, DIP, tip. Index on the thumb side.
    for name, mcp, x in (
        ("INDEX", 5, 0.3),
        ("MIDDLE", 9, 0.1),
        ("RING", 13, -0.1),
        ("PINKY", 17, -0.3),
    ):
        lm[mcp] = (x, -1.0)
        lm[mcp + 1] = (x, -1.4)
        if name in extended:
            lm[mcp + 2] = (x, -1.7)
            lm[mcp + 3] = (x, -1.9)
        else:  # folded back down towards the knuckle
            lm[mcp + 2] = (x + 0.05, -1.25)
            lm[mcp + 3] = (x, -1.1)

    # Rotate so the summed knuckle->tip vector of the extended fingers points
    # exactly at `direction` (same sum as pointing_direction()).
    rotation = 0.0
    tips = {"THUMB": (2, 4), "INDEX": (5, 8), "MIDDLE": (9, 12)}
    tips.update({"RING": (13, 16), "PINKY": (17, 20)})
    if extended:
        dx = sum(lm[tips[f][1]][0] - lm[tips[f][0]][0] for f in extended)
        dy = sum(lm[tips[f][1]][1] - lm[tips[f][0]][1] for f in extended)
        rotation = math.radians(DIRECTIONS[direction]) - math.atan2(dx, -dy)

    c, s = math.cos(rotation), math.sin(rotation)
    ox, oy = origin
    return [(ox + scale * (x * c - y * s), oy + scale * (x * s + y * c)) for x, y in lm]


class RecognizeTests(unittest.TestCase):
    def assertGesture(self, extended, direction, expected):
        self.assertEqual(recognize(make_hand(extended, direction)), expected)

    # --- the gestures intent_node maps to an intent ---
    def test_open_hand_up(self):
        all_fingers = ("THUMB", "INDEX", "MIDDLE", "RING", "PINKY")
        self.assertGesture(all_fingers, "UP", "OPEN_HAND_UP")

    def test_closed_hand(self):
        self.assertGesture((), "UP", "CLOSED_HAND")

    def test_thumb_up(self):
        self.assertGesture(("THUMB",), "UP", "THUMB_UP")

    def test_thumb_down(self):
        self.assertGesture(("THUMB",), "DOWN", "THUMB_DOWN")

    def test_index_left(self):
        self.assertGesture(("INDEX",), "LEFT", "INDEX_LEFT")

    def test_index_right(self):
        self.assertGesture(("INDEX",), "RIGHT", "INDEX_RIGHT")

    def test_index_middle_left(self):
        self.assertGesture(("INDEX", "MIDDLE"), "LEFT", "INDEX_MIDDLE_LEFT")

    def test_index_middle_right(self):
        self.assertGesture(("INDEX", "MIDDLE"), "RIGHT", "INDEX_MIDDLE_RIGHT")

    # --- other shapes still get a predictable name ---
    def test_open_hand_takes_the_direction(self):
        all_fingers = ("THUMB", "INDEX", "MIDDLE", "RING", "PINKY")
        self.assertGesture(all_fingers, "DOWN", "OPEN_HAND_DOWN")

    def test_names_follow_thumb_to_pinky_order(self):
        self.assertGesture(("MIDDLE", "INDEX", "THUMB"), "UP", "THUMB_INDEX_MIDDLE_UP")

    def test_closed_hand_ignores_rotation(self):
        hand = make_hand((), "UP")
        rotated = [(-y, x) for x, y in hand]
        self.assertEqual(recognize(rotated), "CLOSED_HAND")

    def test_same_gesture_at_any_size_and_position(self):
        for scale, origin in ((40.0, (10.0, 10.0)), (900.0, (1500.0, 800.0))):
            hand = make_hand(("INDEX",), "RIGHT", scale=scale, origin=origin)
            self.assertEqual(recognize(hand), "INDEX_RIGHT", (scale, origin))


class MathTests(unittest.TestCase):
    def test_angle(self):
        self.assertAlmostEqual(angle((0, 1), (0, 0), (0, -1)), 180.0)
        self.assertAlmostEqual(angle((1, 0), (0, 0), (0, 1)), 90.0)
        self.assertAlmostEqual(angle((1, 0), (0, 0), (1, 0)), 0.0)

    def test_angle_with_zero_length_side_is_zero(self):
        self.assertEqual(angle((0, 0), (0, 0), (1, 1)), 0)

    def test_finger_extended_threshold(self):
        def finger_bent_to(degrees):
            lm = [(0.0, 0.0)] * 21
            lm[5] = (0.0, 0.0)  # MCP
            lm[6] = (0.0, -1.0)  # PIP
            a = math.radians(degrees)  # angle at PIP between MCP and tip
            lm[8] = (math.sin(a), -1.0 + math.cos(a))
            return lm

        self.assertTrue(is_finger_extended(finger_bent_to(EXTENDED_ANGLE + 1), INDEX))
        self.assertFalse(is_finger_extended(finger_bent_to(EXTENDED_ANGLE - 1), INDEX))

    def test_thumb_uses_thumb_out(self):
        out = make_hand(("THUMB",), "UP")
        tucked = make_hand((), "UP")
        self.assertGreater(thumb_out(out), 0.5)
        self.assertLess(thumb_out(tucked), 0.5)
        self.assertTrue(is_finger_extended(out, THUMB))
        self.assertFalse(is_finger_extended(tucked, THUMB))

    def test_pointing_direction_sectors(self):
        # (dx, dy) of one finger, y down; boundaries are at +-45 and +-135 deg
        cases = {
            (0.0, -1.0): "UP",
            (1.0, 0.0): "RIGHT",
            (0.0, 1.0): "DOWN",
            (-1.0, 0.0): "LEFT",
            (0.9, -1.0): "UP",  # 42 deg
            (1.0, -0.9): "RIGHT",  # 48 deg
            (1.0, 0.9): "RIGHT",  # 132 deg
            (0.9, 1.0): "DOWN",  # 138 deg
        }
        for (dx, dy), expected in cases.items():
            lm = [(0.0, 0.0)] * 21
            lm[8] = (dx, dy)  # INDEX tip, MCP (5) at the origin
            self.assertEqual(pointing_direction(lm, [INDEX]), expected, (dx, dy))


if __name__ == "__main__":
    unittest.main()
