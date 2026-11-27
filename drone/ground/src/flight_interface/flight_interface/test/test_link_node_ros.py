"""ROS 2 tests for the flight_interface lifecycle node (#103): real node, real
topics and services, a recording loopback backend instead of the drone.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select flight_interface --event-handlers console_direct+
"""

import json
import os
import time
import unittest
from unittest import mock

from flight_interface.backends import LoopbackBackend
from flight_interface.guard import NEUTRAL, Setpoint

try:
    import rclpy
    from lifecycle_msgs.msg import State
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.lifecycle import TransitionCallbackReturn
    from std_msgs.msg import String
    from std_srvs.srv import Trigger

    from flight_interface.link_node import FlightInterface
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them).
TEST_DOMAIN_ID = 74


def setpoint_json(pitch=0.0, yaw_rate=0.0):
    """What controller_node publishes on /control/attitude_setpoint: an active
    setpoint (C1.1, F17), throttle inside 0-1."""
    return json.dumps(
        {
            "roll": 0.0,
            "pitch": pitch,
            "yaw_rate": yaw_rate,
            "throttle": 0.5,
            "active": True,
            "reason": "ok",
        }
    )


STREAMING = {
    "esp_state": "STREAMING",
    "override_active": True,
    "mask": 11,
    "airborne": True,
    "fc_link_ok": True,  # E6.1
}


def commanded(pitch):
    """What the guard sends for setpoint_json(pitch) under mask 11 with no
    hover throttle: throttle is not the ground's, so it is the hover value."""
    return Setpoint(0.0, pitch, 0.0, 0.0, True)


# What the node may use on a backend (REQ-SAF-023: nothing that could arm).
# send_neutral and end_session: the E-stop (decision A1); request_resume: the
# operator's ~/resume (decision A3).
ALLOWED = {
    "send_setpoint",
    "send_neutral",
    "end_session",
    "request_resume",
    "poll",
    "take_acks",  # read-only: the ESP's answers, logged (decision E2)
    "close",
    "name",
    "estop_note",  # read-only text: what the E-stop answer says (A1)
    "estop_note_no_neutral",  # read-only text: the same with no neutral (A7)
}
# Data fields the tests themselves read; not calls by the node.
TEST_FIELDS = {"sent", "log", "state", "connected", "closed", "calls", "acks"}


class RecordingBackend(LoopbackBackend):
    """Loopback that records EVERY public attribute the node touches, so a test
    can prove the node never uses anything but the allowed ones (REQ-SAF-023).
    It also offers a forbidden arm_control_request(), to show the recorder
    would catch a call to it."""

    def __init__(self, state=None):
        super().__init__(state=state)
        self.calls = set()

    def __getattribute__(self, name):
        if not name.startswith("_") and name not in TEST_FIELDS:
            object.__getattribute__(self, "calls").add(name)
        return object.__getattribute__(self, name)

    def arm_control_request(self, mask):  # must never be called by the node
        pass


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class FlightInterfaceRosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.backend = RecordingBackend(state=STREAMING)
        self.node = FlightInterface(backend=self.backend)
        self.probe = rclpy.create_node("flight_interface_test_probe")
        self.states = []
        self.probe.create_subscription(
            String,
            "/flight/state",
            lambda m: self.states.append(json.loads(m.data)),
            10,
        )
        self.target_pub = self.probe.create_publisher(
            String, "/control/attitude_setpoint", 10
        )
        self.estop = self.probe.create_client(Trigger, "/flight_interface/estop")
        self.reset = self.probe.create_client(Trigger, "/flight_interface/reset_estop")
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.executor.add_node(self.probe)

    def tearDown(self):
        self.executor.shutdown()
        self.node.destroy_node()
        self.probe.destroy_node()

    def spin(self, seconds, target=None):
        """Spin; publish an active setpoint with pitch `target` (deg) at 20 Hz
        meanwhile."""
        end = time.monotonic() + seconds
        next_pub = 0.0
        while time.monotonic() < end:
            if target is not None and time.monotonic() >= next_pub:
                msg = String()
                msg.data = setpoint_json(pitch=target)
                self.target_pub.publish(msg)
                next_pub = time.monotonic() + 0.05
            self.executor.spin_once(timeout_sec=0.01)

    def call(self, client):
        self.assertTrue(client.wait_for_service(timeout_sec=2.0))
        future = client.call_async(Trigger.Request())
        end = time.monotonic() + 2.0
        while not future.done() and time.monotonic() < end:
            self.executor.spin_once(timeout_sec=0.01)
        return future.result()

    def configure_and_activate(self):
        self.node.trigger_configure()
        self.node.trigger_activate()
        self.assertEqual(self.node._state_machine.current_state[1], "active")

    def sent_since(self, index):
        return [cmd for _, cmd in self.backend.sent[index:]]

    def test_unconfigured_sends_and_publishes_nothing(self):
        self.spin(0.3, target=0.3)
        self.assertEqual(self.backend.sent, [])
        self.assertEqual(self.states, [])

    def test_configured_but_inactive_publishes_state_and_sends_nothing(self):
        self.node.trigger_configure()
        self.spin(0.4, target=0.3)
        self.assertEqual(self.backend.sent, [])
        self.assertTrue(self.states)
        self.assertFalse(self.states[-1]["link_ok"])
        self.assertFalse(self.states[-1]["active"])

    def test_active_streams_the_target_and_reports_link_ok(self):
        self.configure_and_activate()
        self.spin(0.5, target=0.3)
        sent = self.sent_since(0)
        self.assertGreater(len(sent), 10)  # ~50 Hz
        self.assertIn(commanded(0.3), sent)
        self.assertTrue(self.states[-1]["link_ok"])
        self.assertEqual(self.states[-1]["esp_state"], "STREAMING")

    def test_deactivate_sends_one_last_zero_then_nothing(self):
        self.configure_and_activate()
        self.spin(0.3, target=0.3)
        self.node.trigger_deactivate()
        n = len(self.backend.sent)
        self.assertEqual(self.backend.sent[-1][1], NEUTRAL)
        self.spin(0.3, target=0.3)
        self.assertEqual(len(self.backend.sent), n)
        self.assertFalse(self.states[-1]["link_ok"])

    def test_target_stops_gives_zero(self):
        self.configure_and_activate()
        self.spin(0.3, target=0.3)
        self.spin(0.4)  # mission_node silent
        self.assertEqual(self.backend.sent[-1][1], NEUTRAL)

    def test_watchdog_link_lost_gives_zero_and_link_ok_false(self):
        self.configure_and_activate()
        self.spin(0.3, target=0.3)
        self.backend.connected = False  # the drone goes silent
        self.spin(0.8, target=0.3)
        self.assertEqual(self.backend.sent[-1][1], NEUTRAL)
        self.assertFalse(self.states[-1]["link_ok"])
        self.backend.connected = True
        self.spin(0.4, target=0.3)
        self.assertEqual(self.backend.sent[-1][1], commanded(0.3))
        self.assertTrue(self.states[-1]["link_ok"])

    def test_estop_cuts_at_once_and_needs_a_reset_and_a_new_target(self):
        self.configure_and_activate()
        self.spin(0.3, target=0.3)
        response = self.call(self.estop)
        self.assertTrue(response.success)
        n = len(self.backend.sent)
        self.spin(0.3, target=0.3)
        self.assertEqual(len(self.backend.sent), n)  # nothing at all
        self.assertTrue(self.states[-1]["estop"])
        self.assertFalse(self.states[-1]["link_ok"])

        self.assertTrue(self.call(self.reset).success)
        self.assertFalse(self.call(self.reset).success)  # nothing left to reset
        self.spin(0.3, target=0.2)
        self.assertEqual(self.backend.sent[-1][1], commanded(0.2))

    def test_never_asks_for_anything_but_setpoints(self):
        # REQ-SAF-023: every transition, a link loss and recovery, an E-stop and
        # a reset: the node only ever calls send_setpoint, poll and close.
        self.configure_and_activate()
        self.spin(0.2, target=0.1)
        self.backend.connected = False
        self.spin(0.7, target=0.1)
        self.backend.connected = True
        self.spin(0.2, target=0.1)
        self.call(self.estop)
        self.call(self.reset)
        self.node.trigger_deactivate()
        self.node.trigger_activate()
        self.spin(0.2, target=0.1)
        self.node.trigger_deactivate()
        self.node.trigger_cleanup()
        self.assertLessEqual(self.backend.calls, ALLOWED)
        self.assertNotIn("arm_control_request", self.backend.calls)
        for _, cmd in self.backend.sent:
            self.assertIsInstance(cmd, Setpoint)

    def test_cleanup_closes_the_backend_and_shutdown_from_active(self):
        self.configure_and_activate()
        self.spin(0.2, target=0.3)
        self.node.trigger_shutdown()
        self.assertTrue(self.backend.closed)
        self.assertEqual(self.backend.sent[-1][1], NEUTRAL)
        self.assertEqual(
            self.node._state_machine.current_state[0], State.PRIMARY_STATE_FINALIZED
        )

    def test_recorder_catches_a_forbidden_call(self):
        # The REQ-SAF-023 test above would fail if the node ever armed.
        self.backend.arm_control_request(15)
        self.assertIn("arm_control_request", self.backend.calls)
        self.assertFalse(self.backend.calls <= ALLOWED)

    def test_estop_sends_one_zero_and_survives_cleanup_and_configure(self):
        self.configure_and_activate()
        self.spin(0.3, target=0.3)
        self.call(self.estop)
        self.assertEqual(self.backend.sent[-1][1], NEUTRAL)
        self.assertTrue(self.states[-1]["estop"])  # published at once
        n = len(self.backend.sent)
        self.node.trigger_deactivate()
        self.node.trigger_cleanup()
        self.node.trigger_configure()
        self.node.trigger_activate()
        self.spin(0.3, target=0.3)
        self.assertEqual(len(self.backend.sent), n)  # still nothing: latched
        self.assertTrue(self.node.guard.estop)
        self.assertTrue(self.call(self.reset).success)
        self.assertFalse(self.node.guard.estop)

    def publish_raw(self, text, seconds):
        end = time.monotonic() + seconds
        next_pub = 0.0
        while time.monotonic() < end:
            if time.monotonic() >= next_pub:
                msg = String()
                msg.data = text
                self.target_pub.publish(msg)
                next_pub = time.monotonic() + 0.05
            self.executor.spin_once(timeout_sec=0.01)

    def test_a_bad_setpoint_is_dropped_and_goes_neutral(self):
        # F18: malformed input never flies; it goes stale -> neutral.
        self.configure_and_activate()
        self.spin(0.3, target=0.3)
        self.assertEqual(self.backend.sent[-1][1], commanded(0.3))
        bad = json.dumps(
            {"roll": 0, "pitch": 3, "yaw_rate": 0, "throttle": 2.0, "active": True}
        )
        with mock.patch.object(self.node.get_logger(), "warning") as warning:
            self.publish_raw(bad, 0.4)
        self.assertEqual(self.backend.sent[-1][1], NEUTRAL)
        self.assertTrue(
            any("bad attitude setpoint" in c.args[0] for c in warning.call_args_list)
        )

    def test_a_setpoint_beyond_the_caps_is_clamped_with_a_warning(self):
        self.configure_and_activate()
        with mock.patch.object(self.node.get_logger(), "warning") as warning:
            self.spin(0.3, target=40.0)
        self.assertEqual(self.backend.sent[-1][1], commanded(15.0))
        self.assertTrue(
            any("clamped: pitch" in c.args[0] for c in warning.call_args_list)
        )

    def test_bad_hover_throttle_fails_configure_before_the_backend(self):
        for value in (0.95, "high", True):
            with self.subTest(value=value):
                self.node.set_parameters(
                    [rclpy.parameter.Parameter("hover_throttle", value=value)]
                )
                self.assertEqual(
                    self.node.trigger_configure(), TransitionCallbackReturn.FAILURE
                )
                self.assertIsNone(self.node.guard)

    def test_hover_throttle_reaches_the_guard(self):
        self.node.set_parameters(
            [rclpy.parameter.Parameter("hover_throttle", value=0.6)]
        )
        self.configure_and_activate()
        self.assertEqual(self.node.guard.hover_throttle, 0.6)
        self.spin(0.3, target=0.3)
        # Mask 11: throttle is not the ground's, so the hover value goes out.
        self.assertEqual(self.backend.sent[-1][1], Setpoint(0.0, 0.3, 0.0, 0.6, True))

    def test_px4_backend_outside_the_sim_image_fails_configure(self):
        # No px4_msgs here (only the simulation image has it): ImportError ->
        # FAILURE, nothing left half-created. Without a hover throttle the
        # px4 backend refuses first (ValueError), also FAILURE.
        self.node.destroy_node()  # the same node name, one at a time
        for params in ({"hover_throttle": 0.7}, {"hover_throttle": 0.0}):
            node = FlightInterface()
            try:
                node.set_parameters(
                    [rclpy.parameter.Parameter("backend", value="px4")]
                    + [rclpy.parameter.Parameter(k, value=v) for k, v in params.items()]
                )
                with self.subTest(params=params):
                    self.assertEqual(
                        node.trigger_configure(), TransitionCallbackReturn.FAILURE
                    )
                    self.assertIsNone(node.guard)
                    self.assertIsNone(node.backend)
            finally:
                node.destroy_node()
        self.node = FlightInterface(backend=self.backend)  # for tearDown

    def test_bad_backend_parameter_fails_configure(self):
        self.node.destroy_node()  # the same node name, one at a time
        node = FlightInterface()
        try:
            node.set_parameters([rclpy.parameter.Parameter("backend", value="nope")])
            self.assertEqual(node.trigger_configure(), TransitionCallbackReturn.FAILURE)
            self.assertIsNone(node.guard)
        finally:
            node.destroy_node()
        self.node = FlightInterface(backend=self.backend)  # for tearDown


class FailingCloseBackend(LoopbackBackend):
    """close() raises, like a socket error in a real backend."""

    def close(self):
        raise RuntimeError("socket error")


class FailingDestroyBackend(LoopbackBackend):
    """close() works: on_error must still call it when the release failed
    earlier, so BYE goes out instead of waiting for T_loss."""


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class FlightInterfaceErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def test_a_failing_transition_releases_everything(self):
        node = FlightInterface(backend=FailingCloseBackend(state=STREAMING))
        try:
            node.trigger_configure()
            self.assertIsNotNone(node.guard)
            node.trigger_cleanup()  # close() raises -> on_error
            self.assertIsNone(node.guard)
            self.assertIsNone(node.backend)
            self.assertEqual(node._entities, [])
            self.assertEqual(node._state_machine.current_state[1], "unconfigured")
        finally:
            node.destroy_node()

    def test_on_error_still_closes_the_backend_when_the_release_fails(self):
        backend = FailingDestroyBackend(state=STREAMING)
        node = FlightInterface(backend=backend)
        try:
            node.trigger_configure()

            def broken_destroy(entity):
                raise RuntimeError("destroy failed")

            node._entities.insert(0, (broken_destroy, None))
            node.trigger_cleanup()  # _release raises before close -> on_error
            self.assertTrue(backend.closed)  # BYE through close()
            self.assertIsNone(node.backend)
            self.assertIsNone(node.guard)
            self.assertEqual(node._state_machine.current_state[1], "unconfigured")
        finally:
            node.destroy_node()
