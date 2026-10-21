"""ROS 2 tests for intent_node: real node, real topics, no camera.

Starts IntentNode with input_source:=topic, publishes prepared JSON on
/gesture/raw and checks what it publishes on /gesture/intent.
neutral_timeout_sec is raised to 1.0 s so the "unconfirmed" state can be
observed without racing the timeout.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select intent_node --event-handlers console_direct+
or:        python3 -m unittest test.test_intent_node_ros -v   (package folder, ROS sourced)
"""

import json
import os
import time
import unittest

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String

    from intent_node.intent_node import IntentNode
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

ROS_ARGS = [
    "--ros-args",
    "-p", "input_source:=topic",
    "-p", "neutral_timeout_sec:=1.0",
    "-p", "stale_timeout_sec:=2.0",
]  # fmt: skip
KEYS = {"intent", "gesture", "reason", "t", "xy"}
# Own ROS domain: colcon tests packages in parallel, and the other test files,
# running containers or teammates must not see these topics. 81-83 are used
# by gesture_node, intent_node and drone/tests/ros.
TEST_DOMAIN_ID = 82


def packet(gesture, t=1.0, xy=None):
    return json.dumps({"gesture": gesture, "t": t, "xy": xy})


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class IntentNodeTopicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(args=ROS_ARGS, domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.node = IntentNode()
        self.probe = rclpy.create_node("intent_node_test_probe")
        self.pub = self.probe.create_publisher(String, "/gesture/raw", 10)
        self.intents = []
        self.probe.create_subscription(String, "/gesture/intent", self._on_intent, 10)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.executor.add_node(self.probe)
        # Packets published before discovery would be lost
        self.assertTrue(
            self.spin_until(
                lambda: self.pub.get_subscription_count() > 0 and self.intents
            ),
            "intent_node and the test probe never discovered each other",
        )

    def tearDown(self):
        self.executor.shutdown()
        self.node.destroy_node()
        self.probe.destroy_node()

    def _on_intent(self, msg):
        self.intents.append(json.loads(msg.data))

    # ---------- helpers ----------
    def spin_for(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.executor.spin_once(timeout_sec=0.01)

    def spin_until(self, condition, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            self.executor.spin_once(timeout_sec=0.01)
        return bool(condition())

    def send(self, text, n=1, dt=0.05):
        for _ in range(n):
            self.pub.publish(String(data=text))
            self.spin_for(dt)

    def latest(self):
        return self.intents[-1]

    def wait_intent(self, intent, reason=None, timeout=5.0):
        def seen():
            last = self.latest()
            return last["intent"] == intent and reason in (None, last["reason"])

        self.assertTrue(
            self.spin_until(seen, timeout),
            f"expected {intent} ({reason}), last was {self.latest()}",
        )

    # ---------- tests ----------
    def test_starts_with_hover_land_before_any_packet(self):
        self.assertEqual(self.intents[0]["intent"], "HOVER_LAND_REQUEST")
        self.assertEqual(self.intents[0]["reason"], "no_packets_yet")

    def test_five_packets_give_move_right(self):
        self.send(packet("INDEX_RIGHT"), n=5)
        self.wait_intent("MOVE_RIGHT", "ok")
        self.assertEqual(self.latest()["gesture"], "INDEX_RIGHT")

    def test_four_packets_are_not_enough(self):
        self.send(packet("INDEX_RIGHT"), n=4)
        self.spin_for(0.2)
        self.assertEqual(self.latest()["intent"], "NEUTRAL")
        self.assertEqual(self.latest()["reason"], "unconfirmed")
        self.assertNotIn("MOVE_RIGHT", [i["intent"] for i in self.intents])

    def test_silence_goes_neutral_then_hover_land(self):
        self.send(packet("INDEX_RIGHT"), n=5)
        self.wait_intent("MOVE_RIGHT")
        self.wait_intent("NEUTRAL", "no_hand", timeout=3.0)
        start = len(self.intents)
        self.wait_intent("HOVER_LAND_REQUEST", "no_packets", timeout=3.0)
        # Once the hand is gone the old gesture is never replayed
        self.assertNotIn("MOVE_RIGHT", [i["intent"] for i in self.intents[start:]])

    def test_no_hand_packets_go_neutral_then_hover_land(self):
        self.send(packet("INDEX_RIGHT"), n=5)
        self.wait_intent("MOVE_RIGHT")
        self.send(packet(None), n=25, dt=0.05)  # 1.25 s of "no hand"
        self.assertEqual(self.latest()["intent"], "NEUTRAL")
        self.assertEqual(self.latest()["reason"], "no_hand")
        self.send(packet(None), n=25, dt=0.05)  # 2.5 s in total
        self.assertEqual(self.latest()["intent"], "HOVER_LAND_REQUEST")
        self.assertEqual(self.latest()["reason"], "no_hand_long")

    def test_switching_gesture(self):
        self.send(packet("INDEX_RIGHT"), n=5)
        self.wait_intent("MOVE_RIGHT")
        self.send(packet("CLOSED_HAND"), n=5)
        self.wait_intent("STOP_HOVER")

    def test_unknown_gesture_is_neutral(self):
        self.send(packet("PEACE_SIGN"), n=6)
        self.wait_intent("NEUTRAL", "unmapped_gesture")

    def test_bad_datagrams_are_ignored(self):
        for text in ("not json", "[1, 2]", "", '"INDEX_RIGHT"'):
            self.send(text)
        self.spin_for(0.2)
        self.assertEqual(self.latest()["reason"], "no_packets_yet")
        self.send(packet("INDEX_RIGHT"), n=5)
        self.wait_intent("MOVE_RIGHT")

    def test_t_and_xy_are_passed_through(self):
        self.send(packet("INDEX_LEFT", t=12.5, xy=[0.25, 0.75]), n=5)
        self.wait_intent("MOVE_LEFT")
        self.assertEqual(self.latest()["t"], 12.5)
        self.assertEqual(self.latest()["xy"], [0.25, 0.75])

    def test_publishes_json_at_about_20_hz(self):
        before = len(self.intents)
        self.spin_for(1.0)
        received = self.intents[before:]
        self.assertTrue(10 <= len(received) <= 30, f"{len(received)} messages in 1 s")
        for intent in received:
            self.assertEqual(set(intent), KEYS)


if __name__ == "__main__":
    unittest.main()
