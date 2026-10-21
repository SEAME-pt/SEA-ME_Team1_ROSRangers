"""ROS 2 tests for intent_node: real node, real topics, no camera.

Starts IntentNode, feeds it prepared JSON and checks what it publishes on
/gesture/intent. Both inputs are tested: input_source:=topic (/gesture/raw,
what gesture_node uses) and the default UDP socket (the standalone
gestures.py). neutral_timeout_sec is raised to 1.0 s so the "unconfirmed"
state can be observed without racing the timeout.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select intent_node --event-handlers console_direct+
or:        python3 -m unittest test.test_intent_node_ros -v   (package folder, ROS sourced)
"""

import json
import os
import socket
import time
import unittest
from unittest import mock

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String

    from intent_node.intent_node import IntentNode
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

KEYS = {"intent", "gesture", "reason", "t", "xy"}
# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 82


def packet(gesture, t=1.0, xy=None):
    return json.dumps({"gesture": gesture, "t": t, "xy": xy})


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class IntentProbe:
    """IntentNode plus a probe node that feeds it and records /gesture/intent.

    Subclasses provide the input: open_input(), input_ready(), send_raw(),
    close_input().
    """

    @classmethod
    def ros_args(cls):
        return [
            "--ros-args",
            "-p", "neutral_timeout_sec:=1.0",
            "-p", "stale_timeout_sec:=2.0",
        ]  # fmt: skip

    @classmethod
    def setUpClass(cls):
        rclpy.init(args=cls.ros_args(), domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.node = IntentNode()
        self.probe = rclpy.create_node("intent_node_test_probe")
        self.intents = []
        self.probe.create_subscription(String, "/gesture/intent", self._on_intent, 10)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.executor.add_node(self.probe)
        self.open_input()
        # Packets sent before discovery would be lost
        self.assertTrue(
            self.spin_until(lambda: self.input_ready() and self.intents),
            "intent_node and the test probe never discovered each other",
        )

    def tearDown(self):
        self.executor.shutdown()
        self.close_input()
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

    def send(self, data, n=1, dt=0.05):
        for _ in range(n):
            self.send_raw(data)
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

    def link_report(self):
        """Run the 5 s link report now; returns (level, text) of its log line."""
        logger = mock.Mock()
        with mock.patch.object(self.node, "get_logger", return_value=logger):
            self.node._report_link()
        for level in ("info", "warning"):
            log = getattr(logger, level)
            if log.called:
                return level, log.call_args.args[0]
        self.fail("the link report logged nothing")


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class IntentNodeTopicTests(IntentProbe, unittest.TestCase):
    @classmethod
    def ros_args(cls):
        return super().ros_args() + ["-p", "input_source:=topic"]

    def open_input(self):
        self.pub = self.probe.create_publisher(String, "/gesture/raw", 10)

    def input_ready(self):
        return self.pub.get_subscription_count() > 0

    def send_raw(self, text):
        self.pub.publish(String(data=text))

    def close_input(self):
        pass

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

    def test_link_report_names_gesture_node(self):
        level, text = self.link_report()  # nothing received yet
        self.assertEqual(level, "warning")
        self.assertIn("gesture_node", text)
        self.assertNotIn("gestures.py", text)

    def test_link_report_counts_ignored_packets(self):
        self.send("not json", n=2)
        self.send(packet("INDEX_RIGHT"), n=3)
        level, text = self.link_report()
        self.assertEqual(level, "info")
        self.assertIn("2 ignored", text)


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class IntentNodeUdpTests(IntentProbe, unittest.TestCase):
    """The default input: JSON datagrams on UDP from the standalone gestures.py."""

    @classmethod
    def ros_args(cls):
        cls.port = free_udp_port()
        return super().ros_args() + ["-p", f"udp_port:={cls.port}"]

    def open_input(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def input_ready(self):
        return True

    def send_raw(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.sock.sendto(data, ("127.0.0.1", self.port))

    def close_input(self):
        self.sock.close()

    def test_five_datagrams_give_move_right(self):
        self.send(packet("INDEX_RIGHT"), n=5)
        self.wait_intent("MOVE_RIGHT", "ok")

    def test_bad_datagrams_are_ignored(self):
        for data in (b"\xff\xfe not utf-8", b"not json", b"[1, 2]", b""):
            self.send(data)
        self.spin_for(0.2)
        self.assertEqual(self.latest()["reason"], "no_packets_yet")
        self.send(packet("INDEX_RIGHT"), n=5)
        self.wait_intent("MOVE_RIGHT")

    def test_second_node_on_the_same_port_fails(self):
        with self.assertRaises(OSError):
            IntentNode()

    def test_link_report_names_gestures_py(self):
        level, text = self.link_report()
        self.assertEqual(level, "warning")
        self.assertIn("gestures.py", text)


if __name__ == "__main__":
    unittest.main()
