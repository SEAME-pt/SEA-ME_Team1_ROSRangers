"""ROS 2 tests for controller_node: real node, real topics, no drone.

Starts ControllerNode next to a probe node that plays mission_node (on
/mission/velocity_target) and the velocity estimate (on /estimate/velocity)
and records /control/attitude_setpoint. The control law itself is checked
with a fake clock in test_controller_logic.py.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select controller_node --event-handlers console_direct+
or:        python3 -m pytest test -v   (package folder, ROS sourced)
"""

import json
import os
import time
import unittest

try:
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter
    from std_msgs.msg import String

    from controller_node.controller_node import ControllerNode
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them).
TEST_DOMAIN_ID = 86
KEYS = {"roll", "pitch", "yaw_rate", "throttle", "active", "reason"}
HOVER = 0.7


def overrides(params):
    return [Parameter(name, value=value) for name, value in params.items()]


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class ControllerNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.executor = SingleThreadedExecutor()
        self.nodes = []
        self.probe = self.add(rclpy.create_node("controller_probe"))
        self.target_pub = self.probe.create_publisher(
            TwistStamped, "/mission/velocity_target", 10
        )
        self.velocity_pub = self.probe.create_publisher(
            TwistStamped, "/estimate/velocity", 10
        )
        self.received = []
        self.probe.create_subscription(
            String,
            "/control/attitude_setpoint",
            lambda msg: self.received.append((time.monotonic(), json.loads(msg.data))),
            10,
        )

    def tearDown(self):
        self.executor.shutdown()
        for node in self.nodes:
            node.destroy_node()

    def add(self, node):
        self.nodes.append(node)
        self.executor.add_node(node)
        return node

    def start(self, **params):
        return self.add(ControllerNode(parameter_overrides=overrides(params)))

    def spin(self, seconds, target=None, velocity=None):
        """Spin for `seconds`, publishing the target and the measured velocity
        (vx, vy, vz[, wz]) at 50 Hz when given."""
        end = time.monotonic() + seconds
        next_pub = 0.0
        while time.monotonic() < end:
            now = time.monotonic()
            if now >= next_pub:
                for pub, values in (
                    (self.target_pub, target),
                    (self.velocity_pub, velocity),
                ):
                    if values is None:
                        continue
                    msg = TwistStamped()
                    msg.twist.linear.x = float(values[0])
                    msg.twist.linear.y = float(values[1])
                    msg.twist.linear.z = float(values[2])
                    if len(values) > 3:
                        msg.twist.angular.z = float(values[3])
                    pub.publish(msg)
                next_pub = now + 0.02
            self.executor.spin_once(timeout_sec=0.005)

    def test_publishes_at_the_rate_even_inactive(self):
        self.start(rate_hz=50.0)
        self.spin(0.5)  # discovery
        self.received.clear()
        self.spin(1.0)
        self.assertGreaterEqual(len(self.received), 40)
        self.assertLessEqual(len(self.received), 60)
        last = self.received[-1][1]
        self.assertEqual(set(last), KEYS)
        self.assertFalse(last["active"])

    def test_hover_not_measured_by_default(self):
        # F16: the node's default hover_throttle is 0.0 -> always inactive.
        self.start(mask=15)
        self.spin(1.0, target=(0.3, 0.0, 0.0), velocity=(0.0, 0.0, 0.0))
        self.assertEqual(self.received[-1][1]["reason"], "no_hover_throttle")
        self.assertFalse(self.received[-1][1]["active"])

    def test_no_velocity_is_inactive(self):
        self.start(mask=15, hover_throttle=HOVER)
        self.spin(1.0, target=(0.3, 0.0, 0.0))
        self.assertEqual(self.received[-1][1]["reason"], "no_velocity")

    def test_active_with_both_inputs(self):
        self.start(mask=15, hover_throttle=HOVER)
        self.spin(1.0, target=(0.3, 0.0, 0.0), velocity=(0.0, 0.0, 0.0))
        last = self.received[-1][1]
        self.assertTrue(last["active"])
        self.assertEqual(last["reason"], "ok")
        self.assertGreater(last["pitch"], 0.0)  # forward: nose down (REP-103)

    def test_velocity_stops_then_inactive(self):
        self.start(mask=15, hover_throttle=HOVER)
        self.spin(0.8, target=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0))
        self.assertTrue(self.received[-1][1]["active"])
        self.spin(0.5, target=(0.0, 0.0, 0.0))
        self.assertEqual(self.received[-1][1]["reason"], "stale_velocity")

    def test_stop_output_is_inactive(self):
        node = self.start(mask=15, hover_throttle=HOVER)
        self.spin(0.8, target=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0))
        node.stop_output()
        self.spin(0.1)
        stopped = [m for _, m in self.received if m["reason"] == "stopped"]
        self.assertEqual(len(stopped), 1)
        self.assertFalse(stopped[0]["active"])

    def test_bad_parameters_refused(self):
        bad = (
            {"mask": 7},
            {"hover_throttle": 1.5},
            {"tilt_cap_deg": 20.0},
            {"kp_xy": "fast"},
            {"rate_hz": 0.5},
            {"hover_throttle": True},
        )
        for params in bad:
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    ControllerNode(parameter_overrides=overrides(params))


if __name__ == "__main__":
    unittest.main()
