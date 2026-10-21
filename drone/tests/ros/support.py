"""Shared setup for the tests in drone/tests/ros/.

These tests use gesture_node and intent_node together, so they live outside
both packages. They import the packages from the repo sources (no colcon
install needed for the non-ROS tests), and reuse make_hand() from
gesture_node's unit tests to build landmark fixtures.
"""

import os
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "ground" / "src"
for path in (SRC / "gesture_node", SRC / "intent_node", SRC / "gesture_node" / "test"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# The camera is never opened here: stub MediaPipe and OpenCV if missing
for _name in ("mediapipe", "cv2"):
    try:
        __import__(_name)
    except ImportError:
        _stub = types.ModuleType(_name)
        _stub.solutions = types.SimpleNamespace(hands=None, drawing_utils=None)
        sys.modules[_name] = _stub

from test_hand_processing import make_hand  # noqa: E402

FINGER_ORDER = ("THUMB", "INDEX", "MIDDLE", "RING", "PINKY")


def hand_for(gesture):
    """Landmarks of a hand that should be recognised as `gesture`."""
    if gesture == "CLOSED_HAND":
        return make_hand((), "UP")
    *fingers, direction = gesture.split("_")
    if fingers == ["OPEN", "HAND"]:
        fingers = list(FINGER_ORDER)
    return make_hand(tuple(fingers), direction)


def ros_available():
    """True if rclpy imports. With REQUIRE_ROS_TESTS=1 (CI) a failure raises."""
    try:
        import rclpy  # noqa: F401
        import std_msgs.msg  # noqa: F401
    except ImportError:
        if os.environ.get("REQUIRE_ROS_TESTS"):
            raise
        return False
    return True
