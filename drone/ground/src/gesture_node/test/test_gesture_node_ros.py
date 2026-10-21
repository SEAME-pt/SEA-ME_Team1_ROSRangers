"""ROS 2 tests for gesture_node: real node, real topic, no camera.

Creates GestureNode without running the camera loop (gestures.main()) and
calls send_gesture() the way the loop does once per frame, then checks the
JSON that arrives on /gesture/raw.

Needs ROS 2 Jazzy sourced. MediaPipe and OpenCV are stubbed if missing (the
camera is never opened). Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select gesture_node --event-handlers console_direct+
or:        python3 -m unittest test.test_gesture_node_ros -v   (package folder, ROS sourced)
"""

import json
import os
import sys
import time
import types
import unittest

for _name in ("mediapipe", "cv2"):
    try:
        __import__(_name)
    except ImportError:
        _stub = types.ModuleType(_name)
        _stub.solutions = types.SimpleNamespace(hands=None, drawing_utils=None)
        sys.modules[_name] = _stub

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String

    from gesture_node import gesture_sender
    from gesture_node.gesture_node import GestureNode
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None


# Own ROS domain: colcon tests packages in parallel, and the other test files,
# running containers or teammates must not see these topics. 81-83 are used
# by gesture_node, intent_node and drone/tests/ros.
TEST_DOMAIN_ID = 81


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class GestureNodeTopicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.node = GestureNode()
        self.probe = rclpy.create_node("gesture_node_test_probe")
        self.raw = []
        self.probe.create_subscription(
            String, "/gesture/raw", lambda m: self.raw.append(json.loads(m.data)), 10
        )
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.executor.add_node(self.probe)
        self.assertTrue(
            self.spin_until(lambda: self.node.count_subscribers("/gesture/raw") > 0),
            "gesture_node and the test probe never discovered each other",
        )
        self.destroyed = False

    def tearDown(self):
        self.executor.shutdown()
        if not self.destroyed:
            self.node.destroy_node()
        self.probe.destroy_node()

    def spin_until(self, condition, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            self.executor.spin_once(timeout_sec=0.01)
        return bool(condition())

    def frames(self, gesture, seconds, fps=30):
        """Call send_gesture() like the camera loop does, for `seconds`."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            gesture_sender.send_gesture(gesture, None)
            self.executor.spin_once(timeout_sec=1.0 / fps)

    def test_gesture_is_published_as_json(self):
        gesture_sender.send_gesture("CLOSED_HAND", [0.25, 0.75])
        self.assertTrue(self.spin_until(lambda: self.raw))
        (msg,) = self.raw
        self.assertEqual(set(msg), {"gesture", "t", "xy"})
        self.assertEqual(msg["gesture"], "CLOSED_HAND")
        self.assertEqual(msg["xy"], [0.25, 0.75])
        self.assertIsInstance(msg["t"], float)

    def test_no_hand_is_published_as_null(self):
        gesture_sender.send_gesture(None)
        self.assertTrue(self.spin_until(lambda: self.raw))
        self.assertIsNone(self.raw[0]["gesture"])

    def test_change_is_published_at_once(self):
        gesture_sender.send_gesture("INDEX_LEFT")
        gesture_sender.send_gesture("INDEX_RIGHT")
        self.assertTrue(self.spin_until(lambda: len(self.raw) >= 2))
        self.assertEqual(
            [m["gesture"] for m in self.raw], ["INDEX_LEFT", "INDEX_RIGHT"]
        )

    def test_same_gesture_is_repeated_as_keepalive(self):
        # 30 fps for 1 s, keep-alive every 0.1 s -> about 10 packets, not 30
        self.frames("THUMB_UP", 1.0)
        self.spin_until(lambda: False, timeout=0.1)
        self.assertTrue(6 <= len(self.raw) <= 14, f"{len(self.raw)} packets in 1 s")
        self.assertTrue(all(m["gesture"] == "THUMB_UP" for m in self.raw))

    def test_destroy_stops_publishing(self):
        self.node.destroy_node()
        self.destroyed = True
        self.assertIsNone(gesture_sender._publisher["publish"])
        gesture_sender.send_gesture("CLOSED_HAND")  # must not raise


if __name__ == "__main__":
    unittest.main()
