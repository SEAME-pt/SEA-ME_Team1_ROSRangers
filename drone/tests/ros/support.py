"""Shared setup for the tests in drone/tests/ros/.

These tests use gesture_node, intent_node and mission_node together, so they
live outside the packages. They import the packages from the repo sources (no
colcon install needed for the non-ROS tests), and reuse make_hand() from
gesture_node's unit tests to build landmark fixtures.
"""

import os
import signal
import sys
import time
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "ground" / "src"
PACKAGES = (SRC / "gesture_node", SRC / "intent_node", SRC / "mission_node")
for path in (*PACKAGES, SRC / "gesture_node" / "test"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# The camera is never opened here: stub MediaPipe and OpenCV if missing
for _name in ("mediapipe", "cv2"):
    try:
        __import__(_name)
    except ImportError:
        _stub = types.ModuleType(_name)
        _stub.solutions = types.SimpleNamespace(holistic=None, drawing_utils=None)
        sys.modules[_name] = _stub

from test_hand_processing import make_hand  # noqa: E402

from gesture_node.hand_processing import combine_hands, recognize  # noqa: E402

FINGER_ORDER = ("THUMB", "INDEX", "MIDDLE", "RING", "PINKY")


def hand_for(gesture):
    """Landmarks of one hand that should be recognised as `gesture`."""
    if gesture == "CLOSED_HAND":
        return make_hand((), "UP")
    *fingers, direction = gesture.split("_")
    if fingers == ["OPEN", "HAND"]:
        fingers = list(FINGER_ORDER)
    return make_hand(tuple(fingers), direction)


def hands_for(name):
    """(left, right) landmarks for a two-hand name like "L:NONE+R:INDEX_UP".
    A hand named NONE is None (lowered, so it doesn't count)."""
    left, right = name.split("+")
    return tuple(
        None if part[2:] == "NONE" else hand_for(part[2:]) for part in (left, right)
    )


def gesture_name(hands):
    """The name gesture_node sends for (left, right) raised-hand landmarks:
    recognize() on each hand, then combine_hands()."""
    left, right = (recognize(lm) if lm else None for lm in hands)
    return combine_hands(left, right)


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


# ---------- `ros2 launch` as a subprocess ----------
# Start it with start_new_session=True: its nodes then share a process group
# with it, and the group can be stopped like Ctrl+C in a terminal, which
# signals every process in it. Signalling only `ros2 launch` is not enough:
# when its stdin is a terminal it assumes its nodes got Ctrl+C too and does
# not pass the signal on, and SIGKILL cannot be passed on at all. Either way
# the nodes kept running and disturbed the next test runs in the same domain.


def group_alive(pgid):
    """True while any process of the process group `pgid` is still running."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_group_gone(pgid, timeout=5.0):
    end = time.monotonic() + timeout
    while group_alive(pgid) and time.monotonic() < end:
        time.sleep(0.05)
    return not group_alive(pgid)


def kill_group(pgid):
    """SIGKILL every process left in the group (test cleanup)."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
