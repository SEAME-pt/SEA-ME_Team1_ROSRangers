"""End-to-end gesture pipeline with real ROS 2 topics, no camera.

landmarks -> recognize() -> send_gesture() -> gesture_node -> /gesture/raw
-> intent_node (input_source:=topic, default timeouts) -> /gesture/intent

Each "frame" is what the camera loop in gestures.py does: recognise the hand,
call send_gesture(). Only the camera and MediaPipe's landmark detection are
replaced by prepared landmarks.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  python3 -m pytest drone/tests/ros -v        (from the repo root, ROS sourced)
"""

import json
import time
import unittest

from support import hand_for, ros_available

from gesture_node.hand_processing import recognize
from intent_node.gesture_filter import GESTURE_TO_INTENT

if ros_available():
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String

    from gesture_node import gesture_sender
    from gesture_node.gesture_node import GestureNode
    from intent_node.intent_node import IntentNode
else:
    rclpy = None

FPS = 30
# Own ROS domain: colcon tests packages in parallel, and the other test files,
# running containers or teammates must not see these topics. 81-83 are used
# by gesture_node, intent_node and drone/tests/ros.
TEST_DOMAIN_ID = 83


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(
            args=["--ros-args", "-p", "input_source:=topic"],
            domain_id=TEST_DOMAIN_ID,
        )

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.gesture = GestureNode()
        self.intent = IntentNode()
        self.probe = rclpy.create_node("pipeline_test_probe")
        self.intents = []
        self.probe.create_subscription(
            String,
            "/gesture/intent",
            lambda m: self.intents.append(json.loads(m.data)),
            10,
        )
        self.executor = SingleThreadedExecutor()
        for node in (self.gesture, self.intent, self.probe):
            self.executor.add_node(node)
        self.assertTrue(
            self.spin_until(
                lambda: self.gesture.count_subscribers("/gesture/raw") > 0
                and self.intents
            ),
            "the nodes never discovered each other",
        )

    def tearDown(self):
        self.executor.shutdown()
        for node in (self.gesture, self.intent, self.probe):
            node.destroy_node()

    def spin_until(self, condition, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            self.executor.spin_once(timeout_sec=0.01)
        return bool(condition())

    def show(self, gesture, until_intent, reason=None, timeout=3.0):
        """Show `gesture` to the "camera" (None = no hand) until the intent arrives."""
        landmarks = hand_for(gesture) if gesture else None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            name = recognize(landmarks) if landmarks else None
            gesture_sender.send_gesture(name, None)
            frame_end = time.monotonic() + 1.0 / FPS
            while time.monotonic() < frame_end:
                self.executor.spin_once(timeout_sec=0.005)
            last = self.intents[-1]
            if last["intent"] == until_intent and reason in (None, last["reason"]):
                return
        self.fail(
            f"showing {gesture}: expected {until_intent}, last {self.intents[-1]}"
        )

    def test_every_gesture_reaches_its_intent(self):
        for gesture, intent in GESTURE_TO_INTENT.items():
            with self.subTest(gesture=gesture):
                self.show(gesture, intent, "ok")

    def test_hand_lost_goes_neutral_then_hover_land(self):
        self.show("INDEX_RIGHT", "MOVE_RIGHT")
        self.show(None, "NEUTRAL", "no_hand", timeout=1.0)
        start = len(self.intents)
        self.show(None, "HOVER_LAND_REQUEST", "no_hand_long", timeout=3.0)
        self.assertNotIn("MOVE_RIGHT", [i["intent"] for i in self.intents[start:]])

    def test_recogniser_stops_goes_hover_land(self):
        self.show("CLOSED_HAND", "STOP_HOVER")
        # Camera loop ends: gesture_node stops publishing altogether
        self.assertTrue(
            self.spin_until(
                lambda: self.intents[-1]["reason"] == "no_packets", timeout=4.0
            ),
            f"last {self.intents[-1]}",
        )
        self.assertEqual(self.intents[-1]["intent"], "HOVER_LAND_REQUEST")


if __name__ == "__main__":
    unittest.main()
