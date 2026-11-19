"""Unit tests for the gesture maths in hand_processing.py. No ROS or camera needed.

recognize() takes 21 (x, y) landmarks (pixels, y down) and returns a gesture
name. The hands below are built from known finger states and a known
direction, so each test says: "this hand shape must give this gesture".
detect_gesture() runs it on each raised hand Holistic finds and names both.

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
    _mp.solutions = types.SimpleNamespace(holistic=None, drawing_utils=None)
    sys.modules["mediapipe"] = _mp

from gesture_node.hand_processing import (  # noqa: E402
    EXTENDED_ANGLE,
    INDEX,
    LEFT_ELBOW,
    LEFT_WRIST,
    THUMB,
    angle,
    combine_hands,
    create_holistic,
    detect_gesture,
    draw_body,
    is_arm_raised,
    is_finger_extended,
    pointing_direction,
    recognize,
    thumb_out,
)

# Another test module may have installed the stub first: check the module itself
REAL_MEDIAPIPE = getattr(sys.modules["mediapipe"], "__file__", None) is not None

DIRECTIONS = {"UP": 0, "RIGHT": 90, "DOWN": 180, "LEFT": -90}


def make_hand(extended=(), direction="UP", scale=200.0, origin=(320.0, 240.0)):
    """21 landmarks of a hand with the given fingers extended.

    Built pointing up with the wrist at (0, 0) and hand size 1 (wrist to
    middle knuckle), then rotated so the extended fingers point to
    `direction` (a name, or degrees: 0 = up, 90 = right), scaled to pixels
    and moved to `origin`.
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

    # Rotate so the hand points exactly at `direction`, measured like
    # recognize(): the summed knuckle->tip vectors of the extended non-thumb
    # fingers, or the knuckle line (pinky -> index knuckle) for the thumb alone.
    rotation = 0.0
    tips = {"INDEX": (5, 8), "MIDDLE": (9, 12), "RING": (13, 16), "PINKY": (17, 20)}
    lines = [tips[f] for f in extended if f != "THUMB"] or [(17, 5)]
    if extended:
        dx = sum(lm[tip][0] - lm[mcp][0] for mcp, tip in lines)
        dy = sum(lm[tip][1] - lm[mcp][1] for mcp, tip in lines)
        degrees = DIRECTIONS.get(direction, direction)
        rotation = math.radians(degrees) - math.atan2(dx, -dy)

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

    def test_thumb_alone_points_along_the_knuckles(self):
        # Upright hand, thumb out but leaning up (31 deg from vertical), as a
        # real thumb can't stick out at 90 deg. The knuckles point right.
        hand = make_hand(("THUMB",), "RIGHT")
        hand[4] = (320.0 + 200 * 0.95, 240.0 + 200 * -1.3)
        self.assertEqual(recognize(hand), "THUMB_RIGHT")

    def test_thumb_does_not_change_the_direction_of_other_fingers(self):
        # L shape: index up, thumb straight out. Only the index points.
        hand = make_hand(("THUMB", "INDEX"), "UP")
        hand[4] = (320.0 + 200 * 1.4, 240.0 + 200 * -0.2)
        self.assertEqual(recognize(hand), "THUMB_INDEX_UP")

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


# ---------- detect_gesture() with a fake MediaPipe ----------
WIDTH, HEIGHT = 640, 480
IMAGE = types.SimpleNamespace(shape=(HEIGHT, WIDTH, 3))  # only .shape is read


def camera_hand(lm):
    """A hand as Holistic reports it: 0..1 fractions of the camera frame, which
    is NOT mirrored. lm is in the mirrored view, like make_hand() builds it."""
    return types.SimpleNamespace(
        landmark=[types.SimpleNamespace(x=1 - x / WIDTH, y=y / HEIGHT) for x, y in lm]
    )


def holistic_results(left=None, right=None, left_up=True, right_up=True, pose=True):
    """What holistic.process() returns. left/right: landmarks of the person's
    own left/right hand (mirrored view), or None when that hand is not seen.
    *_up: whether that wrist is above its elbow."""
    body = None
    if pose:
        points = [types.SimpleNamespace(x=0.5, y=0.5) for _ in range(33)]
        for elbow, wrist, up in ((13, 15, left_up), (14, 16, right_up)):
            points[elbow] = types.SimpleNamespace(x=0.5, y=0.6)
            points[wrist] = types.SimpleNamespace(x=0.5, y=0.4 if up else 0.8)
        body = types.SimpleNamespace(landmark=points)
    return types.SimpleNamespace(
        pose_landmarks=body,
        left_hand_landmarks=camera_hand(left) if left else None,
        right_hand_landmarks=camera_hand(right) if right else None,
    )


class FakeHolistic:
    def __init__(self, results):
        self.results = results

    def process(self, image):
        return self.results


def detect(results):
    gesture, _ = detect_gesture(FakeHolistic(results), IMAGE)
    return gesture


class DetectGestureTests(unittest.TestCase):
    def test_nobody(self):
        self.assertIsNone(detect(holistic_results(pose=False)))

    def test_both_hands_left_first(self):
        left = make_hand((), "UP", scale=60.0, origin=(200.0, 240.0))
        right = make_hand(("THUMB", "INDEX"), "UP", scale=60.0, origin=(450.0, 240.0))
        self.assertEqual(
            detect(holistic_results(left, right)), "L:CLOSED_HAND+R:THUMB_INDEX_UP"
        )

    def test_one_hand_names_the_other_none(self):
        right = make_hand(("INDEX",), "UP", scale=60.0)
        self.assertEqual(detect(holistic_results(right=right)), "L:NONE+R:INDEX_UP")
        left = make_hand(("INDEX",), "UP", scale=60.0)
        self.assertEqual(detect(holistic_results(left=left)), "L:INDEX_UP+R:NONE")

    def test_lowered_arm_does_not_count(self):
        left = make_hand((), "UP", scale=60.0, origin=(200.0, 240.0))
        right = make_hand(("INDEX",), "UP", scale=60.0, origin=(450.0, 240.0))
        both = holistic_results(left, right, left_up=False)
        self.assertEqual(detect(both), "L:NONE+R:INDEX_UP")
        down = holistic_results(left, right, left_up=False, right_up=False)
        self.assertIsNone(detect(down))

    def test_hand_without_pose_does_not_count(self):
        hand = make_hand(("INDEX",), "UP", scale=60.0)
        self.assertIsNone(detect(holistic_results(hand, hand, pose=False)))

    def test_directions_are_in_the_mirrored_view(self):
        # Holistic sees the camera frame as it is; RIGHT must still be the
        # pilot's right, as in the mirrored window.
        right = make_hand(("INDEX",), "RIGHT", scale=60.0)
        self.assertEqual(detect(holistic_results(right=right)), "L:NONE+R:INDEX_RIGHT")

    def test_landmarks_are_scaled_to_pixels(self):
        # Index finger at 50 deg in pixels = RIGHT. Read as raw 0..1 values on
        # a 4:3 image it would be at 42 deg = UP, so this fails without scaling.
        lm = make_hand(("INDEX",), 50, scale=100.0)
        self.assertEqual(detect(holistic_results(right=lm)), "L:NONE+R:INDEX_RIGHT")

    def test_results_are_returned_for_drawing(self):
        results = holistic_results(pose=False)
        _, returned = detect_gesture(FakeHolistic(results), IMAGE)
        self.assertIs(returned, results)


class TwoHandTests(unittest.TestCase):
    def test_arm_raised_when_wrist_above_elbow(self):
        pose = [types.SimpleNamespace(x=0.5, y=0.5) for _ in range(33)]
        pose[LEFT_ELBOW] = types.SimpleNamespace(x=0.5, y=0.6)
        pose[LEFT_WRIST] = types.SimpleNamespace(x=0.5, y=0.4)
        self.assertTrue(is_arm_raised(pose, LEFT_ELBOW, LEFT_WRIST))
        pose[LEFT_WRIST] = types.SimpleNamespace(x=0.5, y=0.7)
        self.assertFalse(is_arm_raised(pose, LEFT_ELBOW, LEFT_WRIST))

    def test_combine_hands(self):
        self.assertIsNone(combine_hands(None, None))
        self.assertEqual(combine_hands("CLOSED_HAND", None), "L:CLOSED_HAND+R:NONE")
        self.assertEqual(combine_hands(None, "INDEX_UP"), "L:NONE+R:INDEX_UP")
        self.assertEqual(
            combine_hands("OPEN_HAND_UP", "OPEN_HAND_RIGHT"),
            "L:OPEN_HAND_UP+R:OPEN_HAND_RIGHT",
        )


# ---------- the real MediaPipe (venv, CI, gesture Docker image) ----------
@unittest.skipUnless(REAL_MEDIAPIPE, "MediaPipe not installed (stub in use)")
class RealMediaPipeTests(unittest.TestCase):
    def test_blank_frame_has_no_hand(self):
        import numpy as np

        with create_holistic(static_image_mode=True) as holistic:
            frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
            gesture, _ = detect_gesture(holistic, frame)
            self.assertIsNone(gesture)

    def test_draw_body_draws_the_landmarks(self):
        import numpy as np
        from mediapipe.framework.formats import landmark_pb2

        hand = landmark_pb2.NormalizedLandmarkList()
        for x, y in make_hand(("INDEX",), "UP", scale=100.0):
            hand.landmark.add(x=x / WIDTH, y=y / HEIGHT)
        results = types.SimpleNamespace(
            pose_landmarks=None, left_hand_landmarks=hand, right_hand_landmarks=None
        )
        image = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        draw_body(image, results)  # parts not found (None) are skipped
        self.assertGreater(int(image.sum()), 0)


if __name__ == "__main__":
    unittest.main()
