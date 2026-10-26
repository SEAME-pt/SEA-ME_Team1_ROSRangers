"""End-to-end gesture -> mission pipeline with real ROS 2 topics, no camera.

two hands' landmarks -> recognize() + combine_hands() -> send_gesture()
-> gesture_node -> /gesture/raw
-> intent_node (input_source:=topic) -> /gesture/intent -> mission_node
(+ fake_flight_state on /flight/state) -> /mission/velocity_target

Each "frame" is what the camera loop in gestures.py does: recognise the hand,
call send_gesture(). Only the camera and MediaPipe's landmark detection are
replaced by prepared landmarks. Everything else runs with default parameters.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  python3 -m pytest drone/tests/ros -v        (from the repo root, ROS sourced)
"""

import json
import time
import unittest

from support import gesture_name, hands_for, ros_available

if ros_available():
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from mission_node.fake_flight_state import FakeFlightState
    from mission_node.mission_node import MissionNode
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String

    from gesture_node import gesture_sender
    from gesture_node.gesture_node import GestureNode
    from intent_node.intent_node import IntentNode
else:
    rclpy = None

FPS = 30
ZERO = (0.0, 0.0, 0.0, 0.0)
# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 89


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class MissionPipelineTests(unittest.TestCase):
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
        self.nodes = [GestureNode(), IntentNode(), MissionNode(), FakeFlightState()]
        self.probe = rclpy.create_node("mission_pipeline_test_probe")
        self.velocities = []
        self.states = []
        self.probe.create_subscription(
            TwistStamped,
            "/mission/velocity_target",
            lambda m: self.velocities.append(
                (
                    time.monotonic(),
                    (
                        m.twist.linear.x,
                        m.twist.linear.y,
                        m.twist.linear.z,
                        m.twist.angular.z,
                    ),
                )
            ),
            10,
        )
        self.probe.create_subscription(
            String,
            "/mission/state",
            lambda m: self.states.append(json.loads(m.data)),
            10,
        )
        self.executor = SingleThreadedExecutor()
        for node in (*self.nodes, self.probe):
            self.executor.add_node(node)
        self.assertTrue(
            self.spin_until(
                lambda: self.nodes[0].count_subscribers("/gesture/raw") > 0
                and self.state().get("mode") == "READY"
            ),
            f"the nodes never discovered each other, last state {self.state()}",
        )

    def tearDown(self):
        self.executor.shutdown()
        for node in (*self.nodes, self.probe):
            node.destroy_node()

    def state(self):
        return self.states[-1] if self.states else {}

    def vel(self):
        return self.velocities[-1][1] if self.velocities else None

    def spin_until(self, condition, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            self.executor.spin_once(timeout_sec=0.01)
        return bool(condition())

    def show(self, gesture, until, timeout=3.0):
        """Show `gesture` to the "camera" (None = no hand) until until()."""
        hands = hands_for(gesture) if gesture else None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            name = gesture_name(hands) if hands else None
            gesture_sender.send_gesture(name, None)
            frame_end = time.monotonic() + 1.0 / FPS
            while time.monotonic() < frame_end:
                self.executor.spin_once(timeout_sec=0.005)
            if until():
                return
        self.fail(
            f"showing {gesture}: last state {self.state()}, velocity {self.vel()}"
        )

    def mode_is(self, mode):
        return lambda: self.state().get("mode") == mode

    def move_right(self):
        self.show(None, lambda: self.state().get("reason") == "no_hand")
        self.show("L:OPEN_HAND_UP+R:OPEN_HAND_UP", self.mode_is("ENGAGED"))
        self.show("L:INDEX_RIGHT+R:NONE", lambda: self.vel()[1] <= -0.3 + 1e-9)

    def test_gesture_before_open_hand_never_moves(self):
        self.show(
            "L:INDEX_RIGHT+R:NONE", lambda: self.state().get("intent") == "MOVE_RIGHT"
        )
        start = len(self.velocities)
        end = time.monotonic() + 1.0
        self.show("L:INDEX_RIGHT+R:NONE", lambda: time.monotonic() > end)
        self.assertTrue(all(v == ZERO for _, v in self.velocities[start:]))
        self.assertEqual(self.state()["mode"], "READY")

    def test_open_hand_engages_and_index_moves(self):
        self.move_right()
        self.assertEqual(self.vel(), (0.0, -0.3, 0.0, 0.0))
        self.assertEqual(self.state()["gesture"], "L:INDEX_RIGHT+R:NONE")

    def test_index_up_does_not_climb_under_mask_11(self):
        self.move_right()
        self.show("L:NONE+R:INDEX_UP", lambda: self.state().get("intent") == "CLIMB_UP")
        start = len(self.velocities)
        end = time.monotonic() + 1.0
        self.show("L:NONE+R:INDEX_UP", lambda: time.monotonic() > end)
        self.assertTrue(all(v == ZERO for _, v in self.velocities[start:]))
        self.assertIn("axis_not_owned:z", self.state()["limited"])

    def test_closed_hand_stops_and_disengages(self):
        self.move_right()
        self.show("L:NONE+R:CLOSED_HAND", lambda: self.state().get("mode") == "READY")
        self.assertTrue(self.spin_until(lambda: self.vel() == ZERO, 0.5))

    def test_hand_lost_stops_then_requests_landing(self):
        self.move_right()
        start = time.monotonic()
        self.show(None, lambda: self.vel() == ZERO, timeout=1.0)
        # intent_node: NEUTRAL after 0.3 s without a hand, then one tick each
        self.assertLessEqual(time.monotonic() - start, 0.3 + 0.1 + 0.1)
        self.show(None, self.mode_is("LAND_REQUEST"), timeout=3.0)
        self.assertEqual(self.state()["mode_reason"], "no_hand_long")

    def test_recogniser_stops_requests_landing(self):
        self.move_right()
        # Camera loop ends: gesture_node stops publishing altogether
        self.assertTrue(self.spin_until(lambda: self.vel() == ZERO, timeout=1.0))
        self.assertTrue(
            self.spin_until(self.mode_is("LAND_REQUEST"), timeout=4.0), self.state()
        )
        self.assertEqual(self.state()["mode_reason"], "no_packets")


if __name__ == "__main__":
    unittest.main()
