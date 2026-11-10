"""ROS 2 tests of the flight_interface node for the #103 part 2 decisions:
A1 E-stop = one neutral then BYE, A3 operator-only ~/resume, the receive time
of a drone state, REQ-SAF-023 over E-stop and resume, and the node with the
ESP backend against the fake ESP (test/fake_esp.py).

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it). The ESP test also needs sd_protocol.
"""

import json
import os
import time
import unittest

from flight_interface.backends import LoopbackBackend

try:
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String
    from std_srvs.srv import Trigger

    from flight_interface.link_node import FlightInterface
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

try:
    from fake_esp import TEST_PSK, FakeEsp

    from flight_interface.esp_backend import EspBackend
except ImportError:  # no sd_protocol
    FakeEsp = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them).
TEST_DOMAIN_ID = 94

STREAMING = {"esp_state": "STREAMING", "override_active": True, "mask": 8}
FAILSAFE = {"esp_state": "LINK_FAILSAFE", "override_active": False, "mask": None}
ALLOWED = {
    "send_setpoint",
    "send_neutral",
    "end_session",
    "request_resume",
    "poll",
    "close",
    "name",
}
TEST_FIELDS = {
    "sent",
    "log",
    "state",
    "connected",
    "closed",
    "calls",
    "resume_ok",
    "armed_masks",
}
IDLE = {"esp_state": "SESSION_IDLE", "override_active": False, "mask": None}


class RecordingBackend(LoopbackBackend):
    """Loopback that records every public attribute the node touches."""

    def __init__(self, state=None):
        super().__init__(state=state)
        self.calls = set()
        self.resume_ok = True

    def __getattribute__(self, name):
        if not name.startswith("_") and name not in TEST_FIELDS:
            object.__getattribute__(self, "calls").add(name)
        return object.__getattribute__(self, name)

    def request_resume(self, now):
        super().request_resume(now)
        return self.resume_ok


class FailingByeBackend(LoopbackBackend):
    def end_session(self, now):
        raise OSError("socket gone")


class LateStateBackend(LoopbackBackend):
    """Every state was received 1 s ago: the link must not look fresh."""

    def poll(self, now):
        return [dict(self.state, rx_time=now - 1.0)]


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.fake = None
        self.make(RecordingBackend(state=STREAMING))

    def make(self, backend):
        self.backend = backend
        self.node = FlightInterface(backend=backend)
        self.probe = rclpy.create_node("flight_interface_decisions_probe")
        self.states = []
        self.probe.create_subscription(
            String,
            "/flight/state",
            lambda m: self.states.append(json.loads(m.data)),
            10,
        )
        self.target_pub = self.probe.create_publisher(
            TwistStamped, "/mission/velocity_target", 10
        )
        self.clients = {
            name: self.probe.create_client(Trigger, f"/flight_interface/{name}")
            for name in ("estop", "reset_estop", "resume", "arm_control")
        }
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.executor.add_node(self.probe)

    def _destroy_nodes(self):
        self.executor.shutdown()
        self.node.destroy_node()
        self.probe.destroy_node()

    def tearDown(self):
        self._destroy_nodes()
        if self.fake is not None:
            self.fake.close()

    def replace(self, backend):
        self._destroy_nodes()
        self.make(backend)

    def spin(self, seconds, yaw=None):
        end = time.monotonic() + seconds
        next_pub = 0.0
        while time.monotonic() < end:
            if self.fake is not None:
                self.fake.step(time.monotonic())
            if yaw is not None and time.monotonic() >= next_pub:
                msg = TwistStamped()
                msg.twist.angular.z = yaw
                self.target_pub.publish(msg)
                next_pub = time.monotonic() + 0.05
            self.executor.spin_once(timeout_sec=0.005)

    def call(self, name):
        client = self.clients[name]
        self.assertTrue(client.wait_for_service(timeout_sec=2.0))
        future = client.call_async(Trigger.Request())
        end = time.monotonic() + 2.0
        while not future.done() and time.monotonic() < end:
            if self.fake is not None:
                self.fake.step(time.monotonic())
            self.executor.spin_once(timeout_sec=0.005)
        return future.result()

    def activate(self):
        self.node.trigger_configure()
        self.node.trigger_activate()

    def log(self):
        return [what for _, what in self.backend.log]

    # ---------- A1 ----------
    def test_estop_sends_one_neutral_then_bye_then_nothing(self):
        self.activate()
        self.spin(0.3, yaw=0.3)
        self.assertTrue(self.call("estop").success)
        self.assertEqual(self.log()[-2:], ["neutral", "bye"])
        n = len(self.backend.log)
        self.spin(0.3, yaw=0.3)
        self.assertEqual(len(self.backend.log), n)
        self.assertTrue(self.states[-1]["estop"])

    def test_estop_while_inactive_sends_only_bye(self):
        self.node.trigger_configure()
        self.call("estop")
        self.assertEqual(self.log(), ["bye"])

    def test_a_backend_error_cannot_undo_the_estop(self):
        self.replace(FailingByeBackend(state=STREAMING))
        self.activate()
        self.spin(0.2, yaw=0.3)
        self.assertTrue(self.call("estop").success)
        self.assertTrue(self.node.guard.estop)
        n = len(self.backend.sent)
        self.spin(0.2, yaw=0.3)
        self.assertEqual(len(self.backend.sent), n)

    # ---------- A3 ----------
    def test_resume_refused_unless_link_failsafe(self):
        self.activate()
        self.spin(0.2)
        response = self.call("resume")
        self.assertFalse(response.success)
        self.assertIn("not LINK_FAILSAFE", response.message)
        self.spin(1.0)
        self.assertNotIn("resume", self.log())

    def test_resume_goes_out_once_after_the_neutral_window(self):
        self.backend.state = dict(FAILSAFE)
        self.activate()
        self.spin(0.2)
        self.assertNotIn("resume", self.log())  # never by itself
        response = self.call("resume")
        self.assertTrue(response.success, response.message)
        self.spin(0.2)
        self.assertTrue(self.states[-1]["resume_pending"])
        self.assertNotIn("resume", self.log())
        self.spin(1.0)
        self.assertEqual(self.log().count("resume"), 1)
        self.assertFalse(self.states[-1]["resume_pending"])
        self.spin(1.0)
        self.assertEqual(self.log().count("resume"), 1)  # only once

    def test_resume_without_tcp_is_reported_not_raised(self):
        self.backend.state = dict(FAILSAFE)
        self.backend.resume_ok = False
        self.activate()
        self.spin(0.1)
        self.assertTrue(self.call("resume").success)
        self.spin(1.0)
        self.assertEqual(self.log().count("resume"), 1)
        self.assertFalse(self.node.guard.resume_pending)

    # ---------- A4: ARM only on the operator's call ----------
    def test_arm_control_sends_one_arm_with_the_mask_parameter(self):
        self.backend.state = dict(IDLE)
        self.node.set_parameters([rclpy.parameter.Parameter("mask", value=11)])
        self.activate()
        self.spin(0.5)
        self.assertNotIn("arm", self.log())  # never by itself
        response = self.call("arm_control")
        self.assertTrue(response.success, response.message)
        self.assertEqual(self.backend.armed_masks, [11])
        self.spin(0.5)
        self.assertEqual(self.log().count("arm"), 1)

    def test_arm_control_refused_outside_session_idle_or_in_estop(self):
        self.activate()  # the backend reports STREAMING
        self.spin(0.2)
        response = self.call("arm_control")
        self.assertFalse(response.success)
        self.assertIn("not SESSION_IDLE", response.message)
        self.backend.state = dict(IDLE)
        self.spin(0.1)
        self.call("estop")
        self.assertFalse(self.call("arm_control").success)
        self.assertNotIn("arm", self.log())

    def test_arm_control_reports_a_failed_send(self):
        class NoTcp(LoopbackBackend):
            def arm_control(self, mask, now):
                return False

        self.replace(NoTcp(state=IDLE))
        self.activate()
        self.spin(0.2)
        response = self.call("arm_control")
        self.assertFalse(response.success)
        self.assertIn("no TCP", response.message)

    def test_a_bad_mask_parameter_fails_configure(self):
        self.node.set_parameters([rclpy.parameter.Parameter("mask", value=9)])
        from rclpy.lifecycle import TransitionCallbackReturn

        self.assertEqual(
            self.node.trigger_configure(), TransitionCallbackReturn.FAILURE
        )

    # ---------- receive time ----------
    def test_a_state_received_long_ago_is_not_a_fresh_link(self):
        self.replace(LateStateBackend(state=STREAMING))
        self.activate()
        self.spin(0.3, yaw=0.3)
        self.assertFalse(self.states[-1]["link_ok"])
        self.assertEqual(self.backend.sent[-1][1], (0.0, 0.0, 0.0, 0.0))

    # ---------- REQ-SAF-023 ----------
    def test_estop_reset_and_resume_never_use_anything_but_the_allowed_calls(self):
        self.backend.state = dict(FAILSAFE)
        self.activate()
        self.spin(0.2)
        self.call("resume")
        self.spin(0.8)
        self.call("estop")
        self.call("reset_estop")
        self.spin(0.2)
        self.node.trigger_deactivate()
        self.node.trigger_cleanup()
        self.assertLessEqual(self.backend.calls, ALLOWED)
        self.assertIn("end_session", self.backend.calls)
        self.assertIn("request_resume", self.backend.calls)

    # ---------- the node with the ESP backend and the fake ESP ----------
    @unittest.skipIf(FakeEsp is None, "sd_protocol not installed")
    def test_esp_backend_session_estop_and_reconnect_never_arm(self):
        self.fake = FakeEsp()
        self.replace(EspBackend(*self.fake.addr, TEST_PSK))
        self.activate()
        self.spin(0.6, yaw=0.3)
        self.assertTrue(self.backend.session_open)
        self.assertEqual(self.states[-1]["esp_state"], "SESSION_IDLE")
        self.assertTrue(self.states[-1]["link_ok"])
        self.assertGreater(self.fake.setpoints(), 10)
        self.assertEqual(set(self.fake.setpoint_modes), {0})  # neutral until C1

        self.assertTrue(self.call("estop").success)
        self.fake.step(time.monotonic())
        self.assertEqual(self.fake.byes_received, 1)
        n = self.fake.setpoints()
        self.spin(2.6, yaw=0.3)  # the backend opens a new session (A5)
        self.assertEqual(self.fake.setpoints(), n)  # latched: nothing sent
        self.assertEqual(self.backend.sessions_opened, 2)
        self.assertEqual(self.fake.arm_requests, 0)

        self.assertTrue(self.call("reset_estop").success)
        self.spin(0.5, yaw=0.3)
        self.assertGreater(self.fake.setpoints(), n)
        self.assertEqual(self.fake.arm_requests, 0)  # still the operator's

        # A4 + B1: the operator arms; the mask comes back from the ESP's ACK.
        self.assertTrue(self.call("arm_control").success)
        self.spin(0.5, yaw=0.3)
        self.assertEqual(self.fake.arm_requests, 1)
        self.assertEqual(self.states[-1]["esp_state"], "STREAMING")
        self.assertEqual(self.states[-1]["mask"], 8)
        self.assertEqual(set(self.fake.setpoint_modes), {0})  # neutral until C1
