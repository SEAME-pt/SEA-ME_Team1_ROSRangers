"""Run gesture_node's real main() with a fake camera.

Used by test_gesture_node_process.py and docker/smoke_test.sh:

    python3 fake_camera.py MODE [--ros-args ...]

MODE:
  run          frames until the node is stopped (Ctrl+C, SIGTERM)
  quit         q is pressed in the window after 10 frames
  no_camera    camera 0 cannot be opened
  camera_lost  the camera stops delivering frames after 10 frames

Every frame "sees" INDEX_RIGHT, at about 30 FPS. Everything else is the real
gesture_node: rclpy, the spin thread, gestures.main() and gesture_sender.
No webcam, no window, no MediaPipe model.
"""

import sys
import time
import types

for _name in ("mediapipe", "cv2"):
    try:
        __import__(_name)
    except ImportError:
        _stub = types.ModuleType(_name)
        _stub.solutions = types.SimpleNamespace(hands=None, drawing_utils=None)
        sys.modules[_name] = _stub

from gesture_node import gesture_node, gestures  # noqa: E402

MODE = sys.argv[1] if len(sys.argv) > 1 else "run"
FRAMES = 10


class FakeCapture:
    def __init__(self, index):
        self.frames = 0

    def isOpened(self):
        return MODE != "no_camera"

    def read(self):
        self.frames += 1
        if MODE == "camera_lost" and self.frames > FRAMES:
            return False, None
        return True, "frame"

    def release(self):
        pass


class FakeOpenCV:
    COLOR_BGR2RGB = None
    VideoCapture = FakeCapture
    shown = 0

    @staticmethod
    def flip(frame, code):
        return frame

    @staticmethod
    def cvtColor(frame, code):
        return frame

    @classmethod
    def imshow(cls, name, frame):
        cls.shown += 1

    @classmethod
    def waitKey(cls, delay):
        time.sleep(1 / 30)  # frame period of a 30 FPS webcam
        return ord("q") if MODE == "quit" and cls.shown >= FRAMES else -1

    @staticmethod
    def destroyAllWindows():
        pass


class FakeHands:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


gestures.opencv = FakeOpenCV
gestures.create_hands = FakeHands
gestures.detect_gesture = lambda hands, rgb: ("INDEX_RIGHT", None)
gestures.draw_hand = lambda frame, hand: None

if __name__ == "__main__":
    gesture_node.main()
