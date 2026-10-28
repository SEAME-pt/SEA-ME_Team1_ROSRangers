"""Unit tests for the camera loop in gestures.py. No ROS, camera or window.

OpenCV, the camera and MediaPipe are replaced by fakes, so these check the
loop itself: what is sent and printed each frame, that the camera and window
are always released, and why the loop ends (gesture_node turns that reason
into its exit code).

Run with:  python3 -m unittest discover -s test -v      (from the package folder)
or:        colcon test --packages-select gesture_node   (uses pytest)
"""

import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

for _name in ("mediapipe", "cv2"):
    try:
        __import__(_name)
    except ImportError:
        _stub = types.ModuleType(_name)
        _stub.solutions = types.SimpleNamespace(holistic=None, drawing_utils=None)
        sys.modules[_name] = _stub

from gesture_node import gestures  # noqa: E402

Q = ord("q")


class FakeCapture:
    def __init__(self, opened=True, frames=1000):
        self.opened = opened
        self.frames_left = frames
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.frames_left == 0:
            return False, None
        self.frames_left -= 1
        return True, "frame"

    def release(self):
        self.released = True


class FakeOpenCV:
    COLOR_BGR2RGB = "BGR2RGB"

    def __init__(self, capture, keys):
        self.capture = capture
        self.keys = list(keys)  # what waitKey() returns, frame by frame
        self.calls = []
        self.windows_destroyed = False

    def VideoCapture(self, index):
        self.calls.append(("VideoCapture", index))
        return self.capture

    def flip(self, frame, code):
        self.calls.append(("flip", code))
        return "mirrored " + frame

    def cvtColor(self, frame, code):
        self.calls.append(("cvtColor", code))
        return "rgb " + frame

    def imshow(self, name, frame):
        self.calls.append(("imshow", name))
        self.shown = frame

    def waitKey(self, delay):
        return self.keys.pop(0) if self.keys else -1

    def destroyAllWindows(self):
        self.windows_destroyed = True


class FakeHolistic:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CameraLoopTests(unittest.TestCase):
    def run_loop(self, capture=None, keys=(Q,), detections=(), **kwargs):
        """Run gestures.main() with fakes; detections = (gesture, results) per frame."""
        self.capture = capture or FakeCapture()
        self.cv = FakeOpenCV(self.capture, keys)
        self.sent = []
        self.drawn = []
        self.detected_in = []
        detections = list(detections)

        def detect(holistic, rgb):
            self.detected_in.append(rgb)
            return detections.pop(0) if detections else (None, None)

        fakes = {
            "opencv": self.cv,
            "create_holistic": FakeHolistic,
            "detect_gesture": detect,
            "draw_body": lambda frame, results: self.drawn.append((frame, results)),
            "send_gesture": lambda gesture, xy=None: self.sent.append(gesture),
        }
        for name, fake in fakes.items():
            patcher = mock.patch.object(gestures, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.printed = io.StringIO()
        with redirect_stdout(self.printed):
            return gestures.main(**kwargs)

    # --- why the loop ends ---
    def test_missing_camera(self):
        ending = self.run_loop(capture=FakeCapture(opened=False))
        self.assertEqual(ending, gestures.NO_CAMERA)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.capture.released)

    def test_q_quits(self):
        ending = self.run_loop(keys=(-1, -1, Q))
        self.assertEqual(ending, gestures.QUIT)
        self.assertEqual(len(self.sent), 3)

    def test_q_with_extra_key_bits_quits(self):
        # Some backends set modifier bits above the low byte
        self.assertEqual(self.run_loop(keys=(Q | 0x100000,)), gestures.QUIT)

    def test_lost_camera(self):
        ending = self.run_loop(capture=FakeCapture(frames=2), keys=())
        self.assertEqual(ending, gestures.CAMERA_LOST)
        self.assertEqual(len(self.sent), 2)

    def test_should_stop_ends_the_loop(self):
        checks = iter([False, False, False, True])
        ending = self.run_loop(keys=(), should_stop=lambda: next(checks))
        self.assertEqual(ending, gestures.STOPPED)
        self.assertEqual(len(self.sent), 3)

    # --- camera and window are always released ---
    def test_camera_and_window_released_on_every_ending(self):
        for keys, capture in (((Q,), FakeCapture()), ((), FakeCapture(frames=1))):
            with self.subTest(keys=keys):
                self.run_loop(capture=capture, keys=keys)
                self.assertTrue(self.capture.released)
                self.assertTrue(self.cv.windows_destroyed)

    def test_camera_and_window_released_after_an_error(self):
        def broken(holistic, rgb):
            raise RuntimeError("MediaPipe failed")

        with mock.patch.object(gestures, "detect_gesture", broken):
            capture = FakeCapture()
            cv = FakeOpenCV(capture, ())
            with mock.patch.object(gestures, "opencv", cv):
                with mock.patch.object(gestures, "create_holistic", FakeHolistic):
                    with self.assertRaises(RuntimeError):
                        gestures.main()
        self.assertTrue(capture.released)
        self.assertTrue(cv.windows_destroyed)

    # --- what each frame does ---
    def test_opens_camera_0(self):
        self.run_loop()
        self.assertEqual(self.cv.calls[0], ("VideoCapture", 0))

    def test_detects_on_the_camera_frame_and_shows_it_mirrored(self):
        # Not mirrored before detection: Holistic's left/right is then the
        # person's own. Drawn first, then mirrored for the window.
        self.run_loop(detections=[("L:NONE+R:INDEX_UP", "body")])
        self.assertIn(("cvtColor", "BGR2RGB"), self.cv.calls)
        self.assertEqual(self.detected_in, ["rgb frame"])
        self.assertEqual(self.drawn, [("frame", "body")])
        self.assertIn(("flip", 1), self.cv.calls)
        self.assertIn(("imshow", "Gestures"), self.cv.calls)
        self.assertEqual(self.cv.shown, "mirrored frame")

    def test_every_frame_sends_its_gesture(self):
        detections = [
            (None, None),
            ("L:NONE+R:INDEX_RIGHT", "body1"),
            ("L:NONE+R:INDEX_RIGHT", "body2"),
            ("L:CLOSED_HAND+R:INDEX_UP", "body3"),
        ]
        self.run_loop(keys=(-1, -1, -1, Q), detections=detections)
        self.assertEqual(
            self.sent,
            [
                None,
                "L:NONE+R:INDEX_RIGHT",
                "L:NONE+R:INDEX_RIGHT",
                "L:CLOSED_HAND+R:INDEX_UP",
            ],
        )
        self.assertEqual([r for _, r in self.drawn], ["body1", "body2", "body3"])

    def test_only_changes_are_printed(self):
        detections = [
            ("L:NONE+R:INDEX_RIGHT", "b"),
            ("L:NONE+R:INDEX_RIGHT", "b"),
            (None, None),
            ("L:CLOSED_HAND+R:NONE", "b"),
        ]
        self.run_loop(keys=(-1, -1, -1, Q), detections=detections)
        self.assertEqual(
            self.printed.getvalue().split(),
            ["L:NONE+R:INDEX_RIGHT", "L:CLOSED_HAND+R:NONE"],
        )


if __name__ == "__main__":
    unittest.main()
