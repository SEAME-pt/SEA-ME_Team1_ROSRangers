"""ROS 2 tests of the flight_interface node with the ESP backend against the
fake ESP (test/fake_esp.py): the whole path operator service -> node -> wire
-> fake gateway -> STATE -> /flight/state, for the safety cases of #103.

  * A2 + A3: silence trips the watchdog (link_ok false, no latch); the ESP's
    link failsafe shows on /flight/state; nothing resumes by itself; the
    operator's ~/resume brings STREAMING back with RESUME_REQUEST on the wire.
  * K1 + A4 (#179): FC link lost sets link_ok false and refuses
    ~/arm_control; FC link restored opens both again.
  * A4 + B1: a mask the gateway does not run (it runs 8 only) is refused by
    the ESP and never reaches /flight/state.
  * A1 without TCP: BYE cannot go out, but the setpoints stop, so the ESP
    still enters its failsafe; the latch holds over the new session; no ARM.
  * A5 while inactive: an ESP restart gives a new session before activate.
  * configure fails cleanly with a bad esp_port / esp_host or without a PSK,
    and works from the parameters with a good one.

Needs ROS 2 Jazzy sourced and sd_protocol. Without rclpy the tests are
skipped, unless REQUIRE_ROS_TESTS=1 is set (CI sets it).
"""

import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

try:
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.lifecycle import TransitionCallbackReturn
    from rclpy.parameter import Parameter
    from std_msgs.msg import String
    from std_srvs.srv import Trigger

    from flight_interface.link_node import FlightInterface
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

try:
    from fake_esp import TEST_PSK, FakeEsp

    from flight_interface.esp_backend import (
        FC_LINK_LOST,
        FC_LINK_RESTORED,
        EspBackend,
    )
    from sd_protocol.frame import MsgType
    from sd_protocol.messages import AckResult, EspState
except ImportError:  # no sd_protocol
    FakeEsp = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them).
TEST_DOMAIN_ID = 95


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
@unittest.skipIf(FakeEsp is None, "sd_protocol not installed")
class EspNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.fake = FakeEsp()
        self.make(EspBackend(*self.fake.addr, TEST_PSK))

    def make(self, backend):
        """The node with `backend`, or with the one its parameters name (None)."""
        self.node = FlightInterface(backend=backend)
        self.probe = rclpy.create_node("flight_interface_esp_probe")
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

    @property
    def backend(self):
        return self.node.backend

    def _destroy(self):
        if self.node.backend is not None:
            self.node.backend.close()  # destroy_node() does not clean up
        self.executor.shutdown()
        self.node.destroy_node()
        self.probe.destroy_node()

    def tearDown(self):
        self._destroy()
        self.fake.close()

    def node_from_parameters(self, **params):
        self._destroy()
        self.make(None)
        self.node.set_parameters(
            [Parameter("backend", value="esp")]
            + [Parameter(name, value=value) for name, value in params.items()]
        )

    def spin(self, seconds, yaw=None):
        end = time.monotonic() + seconds
        next_pub = 0.0
        while time.monotonic() < end:
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
            self.fake.step(time.monotonic())
            self.executor.spin_once(timeout_sec=0.005)
        return future.result()

    def activate(self):
        self.node.trigger_configure()
        self.node.trigger_activate()

    def streaming(self):
        """Active, then the operator's ARM (mask 8): the ESP streams."""
        self.activate()
        self.spin(0.5, yaw=0.3)
        self.assertTrue(self.call("arm_control").success)
        self.spin(0.4, yaw=0.3)
        state = self.states[-1]
        self.assertEqual(state["esp_state"], "STREAMING")
        self.assertEqual(state["mask"], 8)
        self.assertTrue(state["link_ok"])

    # ---------- A2 + A3 ----------
    def test_watchdog_then_link_failsafe_then_operator_resume(self):
        self.streaming()

        # A2: the ground hears nothing for longer than the 0.5 s watchdog.
        self.fake.send_state = False
        self.spin(0.8, yaw=0.3)
        self.assertFalse(self.states[-1]["link_ok"])
        self.assertIsNone(self.states[-1]["esp_state"])
        self.assertFalse(self.node.guard.estop)  # no latch
        self.fake.send_state = True
        self.spin(0.3, yaw=0.3)
        self.assertTrue(self.states[-1]["link_ok"])

        # The ESP hears nothing for T_loss: its own link failsafe.
        self.fake.accept_udp = False
        self.spin(0.4, yaw=0.3)
        self.fake.accept_udp = True
        self.spin(1.5, yaw=0.3)
        self.assertEqual(self.fake.state, EspState.LINK_FAILSAFE)
        self.assertEqual(self.states[-1]["esp_state"], "LINK_FAILSAFE")
        self.assertTrue(self.states[-1]["failsafe_output"])
        self.assertEqual(self.fake.count(MsgType.RESUME_REQUEST), 0)  # never by itself

        # A3: only the operator's ~/resume, after the neutral window.
        response = self.call("resume")
        self.assertTrue(response.success, response.message)
        self.spin(1.2, yaw=0.3)
        self.assertEqual(self.fake.count(MsgType.RESUME_REQUEST), 1)
        self.assertEqual(self.backend.last_ack, ("RESUME_ACK", AckResult.OK))
        self.assertEqual(self.fake.state, EspState.STREAMING)
        state = self.states[-1]
        self.assertEqual(state["esp_state"], "STREAMING")
        self.assertFalse(state["failsafe_output"])
        self.assertEqual(state["mask"], 8)  # same session: the mask holds
        self.assertEqual(self.fake.arm_requests, 1)  # only the operator's

    # ---------- K1 + A4: FC link health ----------
    def test_fc_link_lost_closes_the_gate_and_refuses_arm(self):
        self.activate()
        self.spin(0.5, yaw=0.3)
        self.assertTrue(self.states[-1]["link_ok"])
        self.assertIsNone(self.states[-1]["fc_link_ok"])

        self.fake.send_event(FC_LINK_LOST)
        self.spin(0.3, yaw=0.3)
        self.assertIs(self.states[-1]["fc_link_ok"], False)
        self.assertFalse(self.states[-1]["link_ok"])  # mission_node goes IDLE
        response = self.call("arm_control")
        self.assertFalse(response.success)
        self.assertIn("FC link", response.message)
        self.spin(0.2)
        self.assertEqual(self.fake.arm_requests, 0)

        self.fake.send_event(FC_LINK_RESTORED)
        self.spin(0.3, yaw=0.3)
        self.assertIs(self.states[-1]["fc_link_ok"], True)
        self.assertTrue(self.states[-1]["link_ok"])
        self.assertTrue(self.call("arm_control").success)
        self.spin(0.4, yaw=0.3)
        self.assertEqual(self.states[-1]["esp_state"], "STREAMING")
        self.assertEqual(self.fake.arm_requests, 1)

    # ---------- A4 + B1: the mask ----------
    def test_a_mask_the_gateway_does_not_run_never_reaches_flight_state(self):
        """~/arm_control answers once ARM is SENT; the ESP's MASK_MISMATCH
        shows only as no mask and SESSION_IDLE on /flight/state."""
        self.node.set_parameters([Parameter("mask", value=11)])
        self.activate()
        self.spin(0.5, yaw=0.3)
        response = self.call("arm_control")
        self.assertTrue(response.success, response.message)
        self.spin(0.4, yaw=0.3)
        self.assertEqual(
            self.backend.last_ack, ("ARM_CONTROL_ACK", AckResult.MASK_MISMATCH)
        )
        state = self.states[-1]
        self.assertEqual(state["esp_state"], "SESSION_IDLE")
        self.assertIsNone(state["mask"])
        self.assertFalse(state["override_active"])
        self.assertEqual(set(self.fake.setpoint_modes), {0})

    # ---------- A1 without TCP ----------
    def test_estop_without_tcp_still_ends_control_and_never_arms(self):
        self.streaming()
        self.fake.close_tcp()  # STREAMING goes on over UDP (ADR-005)
        self.spin(0.3, yaw=0.3)
        self.assertFalse(self.backend.tcp_open)
        self.assertEqual(self.states[-1]["esp_state"], "STREAMING")

        self.assertTrue(self.call("estop").success)
        n = self.fake.setpoints()
        self.spin(0.5, yaw=0.3)
        self.assertEqual(self.fake.byes_received, 0)  # no TCP for BYE
        self.assertEqual(self.fake.setpoints(), n)  # latched: nothing sent
        self.assertEqual(self.fake.state, EspState.LINK_FAILSAFE)  # T_loss
        self.assertTrue(self.fake.failsafe_output)

        self.spin(2.5, yaw=0.3)  # A5: a new session, confirmed by CONFIG_GET
        self.assertEqual(self.backend.sessions_opened, 2)
        self.assertEqual(self.fake.switches, 1)
        self.assertEqual(self.fake.state, EspState.SESSION_IDLE)
        self.assertTrue(self.fake.failsafe_output)  # ADR-004 output goes on
        self.assertEqual(self.fake.setpoints(), n)  # still latched
        self.assertTrue(self.states[-1]["estop"])

        self.assertTrue(self.call("reset_estop").success)
        self.spin(0.5, yaw=0.3)
        self.assertGreater(self.fake.setpoints(), n)
        state = self.states[-1]
        self.assertEqual(state["esp_state"], "SESSION_IDLE")
        self.assertIsNone(state["mask"])
        self.assertTrue(state["failsafe_output"])
        self.assertEqual(self.fake.arm_requests, 1)  # the first, by the operator

    # ---------- A5 while inactive ----------
    def test_an_inactive_node_has_a_new_session_before_activate(self):
        self.node.trigger_configure()
        self.spin(0.5)
        self.assertTrue(self.backend.session_open)
        self.fake.restart()
        self.spin(2.6)
        self.assertEqual(self.backend.sessions_opened, 2)
        self.assertEqual(self.fake.session_id, self.backend.session_id)
        self.node.trigger_activate()
        self.spin(0.4)
        self.assertTrue(self.states[-1]["link_ok"])  # no 3 s wait first
        self.assertEqual(self.fake.arm_requests, 0)

    # ---------- configure from the parameters ----------
    def test_a_bad_esp_address_fails_configure(self):
        for params in ({"esp_port": 70000}, {"esp_port": 0}, {"esp_host": " "}):
            with self.subTest(**params):
                self.node_from_parameters(**params)
                with mock.patch.dict(os.environ, {"SEAME_PSK": TEST_PSK.hex()}):
                    self.assertEqual(
                        self.node.trigger_configure(), TransitionCallbackReturn.FAILURE
                    )
                self.assertIsNone(self.node.backend)

    def test_no_psk_fails_configure_and_nothing_reaches_the_esp(self):
        self.node_from_parameters(esp_host="127.0.0.1", esp_port=self.fake.addr[1])
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"SEAME_PSK": ""}), mock.patch(
                "sd_protocol.ground.SECRETS", pathlib.Path(home) / "secrets.json"
            ):
                self.assertEqual(
                    self.node.trigger_configure(), TransitionCallbackReturn.FAILURE
                )
        self.spin(0.3)
        self.assertEqual(self.fake.received, [])

    def test_the_parameters_reach_the_esp(self):
        self.node_from_parameters(esp_host="localhost", esp_port=self.fake.addr[1])
        with mock.patch.dict(os.environ, {"SEAME_PSK": TEST_PSK.hex()}):
            self.assertEqual(
                self.node.trigger_configure(), TransitionCallbackReturn.SUCCESS
            )
        self.node.trigger_activate()
        self.spin(0.5)
        self.assertEqual(self.backend.name, "esp")
        self.assertEqual(self.fake.hellos, 1)
        self.assertTrue(self.states[-1]["link_ok"])
        self.assertEqual(self.fake.arm_requests, 0)


if __name__ == "__main__":
    unittest.main()
