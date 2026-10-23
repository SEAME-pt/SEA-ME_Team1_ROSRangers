"""ROS 2 tests for mission_node: real node, real topics, no drone.

Starts MissionNode next to a probe node that plays intent_node (on
/gesture/intent) and flight_interface (on /flight/state) at 20 Hz and records
/mission/velocity_target and /mission/state. Also checks the two pretend
input nodes, fake_flight_state and fake_intent, and drives the real
MissionNode through every fault fake_flight_state can produce.

Timing checks allow TRANSPORT_SLACK_SEC on top of the logic's numbers for DDS
delivery and timer jitter. The exact numbers are checked with a fake clock in
test_mission_logic.py.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select mission_node --event-handlers console_direct+
or:        python3 -m unittest test.test_mission_node_ros -v   (package folder, ROS sourced)
"""

import json
import math
import os
import time
import unittest
from unittest import mock

try:
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from mission_node.fake_flight_state import FakeFlightState
    from mission_node.fake_intent import FakeIntent
    from mission_node.mission_node import MissionNode
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter
    from std_msgs.msg import String
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

from mission_node.fake_inputs import flight_state_json, intent_json, script_duration

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 87
PUBLISH_PERIOD = 0.05  # the probe publishes like intent_node: 20 Hz
TRANSPORT_SLACK_SEC = 0.05
ZERO = (0.0, 0.0, 0.0, 0.0)
YAW_20 = math.radians(20.0)
INTENT_KEYS = {"intent", "gesture", "reason", "t", "xy"}
STATE_KEYS = {
    "mode",
    "mode_reason",
    "intent",
    "reason",
    "gesture",
    "mask",
    "airborne",
    "land_request",
    "limited",
    "intent_age_ms",
}
FLIGHT_OK = dict(
    link_ok=True, esp_state="STREAMING", override_active=True, mask=11, airborne=True
)
TOPICS = dict(
    intent_topic="/gesture/intent",
    flight_state_topic="/flight/state",
    velocity_topic="/mission/velocity_target",
    state_topic="/mission/state",
)


def velocity(msg):
    twist = msg.twist
    return (twist.linear.x, twist.linear.y, twist.linear.z, twist.angular.z)


def overrides(params):
    return [Parameter(name, value=value) for name, value in params.items()]


class RosTestCase(unittest.TestCase):
    """rclpy in the test domain, one executor, and spin helpers."""

    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.executor = SingleThreadedExecutor()
        self.nodes = []

    def tearDown(self):
        self.executor.shutdown()
        for node in self.nodes:
            node.destroy_node()

    def add(self, node):
        self.nodes.append(node)
        self.executor.add_node(node)
        return node

    def spin_for(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.executor.spin_once(timeout_sec=0.002)

    def spin_until(self, condition, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            self.executor.spin_once(timeout_sec=0.002)
        return bool(condition())


class Recorder:
    """Subscribes a probe to the mission outputs and keeps what arrives."""

    def record_outputs(self, probe, velocity_topic, state_topic):
        self.velocities = []  # (time received, (vx, vy, vz, wz), msg)
        self.states = []  # (time received, dict)
        probe.create_subscription(
            TwistStamped,
            velocity_topic,
            lambda m: self.velocities.append((time.monotonic(), velocity(m), m)),
            10,
        )
        probe.create_subscription(
            String,
            state_topic,
            lambda m: self.states.append((time.monotonic(), json.loads(m.data))),
            10,
        )

    def vel(self):
        return self.velocities[-1][1] if self.velocities else None

    def state(self):
        return self.states[-1][1] if self.states else {}

    def mode_is(self, mode, reason=None):
        return lambda: self.state().get("mode") == mode and reason in (
            None,
            self.state().get("mode_reason"),
        )


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class MissionProbe(Recorder, RosTestCase):
    """MissionNode plus a probe that plays intent_node and flight_interface."""

    params = {}

    def setUp(self):
        super().setUp()
        topics = {name: self.params.get(name, topic) for name, topic in TOPICS.items()}
        self.node = self.add(MissionNode(parameter_overrides=overrides(self.params)))
        self.probe = self.add(rclpy.create_node("mission_node_test_probe"))
        self.intent_pub = self.probe.create_publisher(
            String, topics["intent_topic"], 10
        )
        self.flight_pub = self.probe.create_publisher(
            String, topics["flight_state_topic"], 10
        )
        self.record_outputs(self.probe, topics["velocity_topic"], topics["state_topic"])
        self.flight = dict(FLIGHT_OK)
        self.last_intent_sent = None
        self.assertTrue(
            self.spin_until(
                lambda: self.intent_pub.get_subscription_count() > 0
                and self.flight_pub.get_subscription_count() > 0
                and self.velocities
                and self.states
            ),
            "mission_node and the test probe never discovered each other",
        )

    def feed(
        self,
        seconds,
        intent=None,
        reason="ok",
        flight=True,
        until=None,
        raw_intent=None,
        raw_flight=None,
    ):
        """Spin and publish at 20 Hz: `intent` (None: nothing) and the flight
        state (flight=False: nothing); raw_* are sent as is instead. Stops
        after `seconds`, or earlier once until() is true. Returns until()."""
        end = time.monotonic() + seconds
        next_publish = time.monotonic()
        while time.monotonic() < end:
            if until is not None and until():
                return True
            if time.monotonic() >= next_publish:
                text = raw_intent
                if text is None and intent is not None:
                    text = intent_json(intent, reason, time.time())
                if text is not None:
                    self.intent_pub.publish(String(data=text))
                    self.last_intent_sent = time.monotonic()
                if flight:
                    if raw_flight is None:
                        raw = flight_state_json(**self.flight)
                    else:
                        raw = raw_flight
                    self.flight_pub.publish(String(data=raw))
                next_publish += PUBLISH_PERIOD
            self.executor.spin_once(timeout_sec=0.002)
        return until is None or bool(until())

    def wait(self, condition, timeout=3.0, message="", **feed):
        self.assertTrue(
            self.feed(timeout, until=condition, **feed),
            f"{message} last state {self.state()}, velocity {self.vel()}",
        )

    def engage(self):
        self.feed(0.3, "NEUTRAL")
        self.wait(self.mode_is("ENGAGED"), intent="HOLD_POSITION")

    def move_right(self):
        self.engage()
        self.wait(lambda: self.vel()[1] <= -0.3 + 1e-9, intent="MOVE_RIGHT")

    def count_velocities(self, seconds, **feed):
        start = len(self.velocities)
        self.feed(seconds, **feed)
        return len(self.velocities) - start


class MissionNodeTests(MissionProbe):
    # REQ-SW-001
    def test_idle_without_inputs_publishes_zero_at_20_hz(self):
        start = len(self.velocities)
        self.feed(1.0, flight=False)
        received = self.velocities[start:]
        self.assertTrue(14 <= len(received) <= 26, f"{len(received)} messages in 1 s")
        stamps = []
        for _, v, msg in received:
            self.assertEqual(v, ZERO)
            self.assertEqual((msg.twist.angular.x, msg.twist.angular.y), (0.0, 0.0))
            self.assertEqual(msg.header.frame_id, "base_link")
            stamps.append(msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9)
        self.assertTrue(all(b > a for a, b in zip(stamps, stamps[1:])), stamps)
        self.assertEqual(self.state()["mode"], "IDLE")
        self.assertEqual(self.state()["mode_reason"], "no_flight_state")

    # REQ-SW-001
    def test_publishes_at_20_hz_in_every_mode(self):
        self.feed(0.3, "NEUTRAL")
        self.assertEqual(self.state()["mode"], "READY")
        rates = {"READY": self.count_velocities(1.0, intent="NEUTRAL")}
        self.engage()
        rates["ENGAGED"] = self.count_velocities(1.0, intent="MOVE_RIGHT")
        self.wait(
            self.mode_is("LAND_REQUEST"),
            intent="HOVER_LAND_REQUEST",
            reason="no_hand_long",
        )
        rates["LAND_REQUEST"] = self.count_velocities(
            1.0, intent="HOVER_LAND_REQUEST", reason="no_hand_long"
        )
        self.wait(self.mode_is("IDLE"), flight=False)
        rates["IDLE"] = self.count_velocities(1.0, flight=False)
        for mode, count in rates.items():
            self.assertTrue(14 <= count <= 26, f"{count} messages in 1 s in {mode}")

    def test_stray_gesture_never_moves(self):
        self.feed(0.3, "NEUTRAL")
        start = len(self.velocities)
        self.feed(1.0, "MOVE_RIGHT")
        self.assertTrue(all(v == ZERO for _, v, _ in self.velocities[start:]))
        self.assertEqual(self.state()["mode"], "READY")

    def test_move_right_ramps_up_on_one_axis(self):
        self.engage()
        start = len(self.velocities)
        self.wait(lambda: self.vel()[1] <= -0.3 + 1e-9, intent="MOVE_RIGHT")
        received = self.velocities[start:]
        moving = [t for t, v, _ in received if v[1] != 0.0]
        self.assertGreaterEqual(moving[-1] - moving[0], 0.45)  # 0.6 s at 0.5 m/s²
        previous = 0.0
        for _, v, _ in received:
            self.assertEqual((v[0], v[2], v[3]), (0.0, 0.0, 0.0))
            self.assertLessEqual(abs(v[1]) - abs(previous), 0.5 * 0.2 + 1e-9)
            previous = v[1]
        state = self.state()
        self.assertEqual(
            (state["mode"], state["intent"], state["gesture"], state["reason"]),
            ("ENGAGED", "MOVE_RIGHT", "INDEX_RIGHT", "ok"),
        )

    def test_yaw_left_is_in_rad_per_s(self):
        self.engage()
        self.wait(lambda: abs(self.vel()[3] - YAW_20) < 1e-9, intent="YAW_LEFT")
        self.assertAlmostEqual(self.vel()[3], 0.349, places=3)

    # REQ-SAF-018
    def test_mask_11_never_commands_z(self):
        self.engage()
        start = len(self.velocities)
        self.feed(1.0, "CLIMB_SLOWLY")
        self.assertTrue(all(v[2] == 0.0 for _, v, _ in self.velocities[start:]))
        self.assertIn("axis_not_owned:z", self.state()["limited"])

    # REQ-SAF-018
    def test_mask_8_only_yaw(self):
        self.flight["mask"] = 8
        self.engage()
        start = len(self.velocities)
        self.feed(1.0, "MOVE_LEFT")
        self.assertTrue(all(v == ZERO for _, v, _ in self.velocities[start:]))
        self.assertIn("axis_not_owned:y", self.state()["limited"])
        self.wait(lambda: self.vel()[3] > 0.0, intent="YAW_LEFT")

    # REQ-SAF-017
    def test_stale_intent_gives_zero_within_0_25_s(self):
        self.move_right()
        last_sent = self.last_intent_sent
        start = len(self.velocities)
        self.wait(lambda: self.vel() == ZERO, timeout=1.0)  # intents stop
        first_zero = next(t for t, v, _ in self.velocities[start:] if v == ZERO)
        self.assertLessEqual(first_zero - last_sent, 0.25 + TRANSPORT_SLACK_SEC)
        self.wait(lambda: self.state()["reason"] == "intent_stale", timeout=0.5)

    def test_lost_intent_topic_requests_landing(self):
        self.move_right()
        self.wait(self.mode_is("LAND_REQUEST"), timeout=3.5)
        self.assertEqual(self.state()["mode_reason"], "intent_topic_lost")
        self.assertTrue(self.state()["land_request"])
        self.assertEqual(self.vel(), ZERO)

    def test_lost_flight_state_goes_idle(self):
        self.move_right()
        start = time.monotonic()
        self.wait(self.mode_is("IDLE"), timeout=2.0, intent="MOVE_RIGHT", flight=False)
        self.assertLessEqual(
            time.monotonic() - start, 0.5 + PUBLISH_PERIOD * 2 + TRANSPORT_SLACK_SEC
        )
        self.assertEqual(self.state()["mode_reason"], "flight_state_stale")
        self.assertEqual(self.vel(), ZERO)

    def test_override_off_stops_at_once(self):
        self.move_right()
        self.flight["override_active"] = False
        start = time.monotonic()
        self.wait(lambda: self.vel() == ZERO, timeout=1.0, intent="MOVE_RIGHT")
        self.assertLessEqual(
            time.monotonic() - start, PUBLISH_PERIOD * 2 + TRANSPORT_SLACK_SEC
        )
        self.wait(
            self.mode_is("IDLE", "override_off"), timeout=0.5, intent="MOVE_RIGHT"
        )

    def test_bad_intent_json_stops_and_is_counted(self):
        self.move_right()
        self.wait(lambda: self.vel() == ZERO, timeout=1.0, raw_intent="{not json")
        self.wait(lambda: self.state()["reason"] == "bad_json", raw_intent="{not json")
        self.assertEqual(self.state()["intent"], "NEUTRAL")
        self.assertGreater(self.node._logic.bad_json, 0)

    def test_flight_state_that_is_not_json_goes_idle(self):
        self.feed(0.3, "NEUTRAL")
        self.wait(
            self.mode_is("IDLE", "flight_state_stale"),
            intent="NEUTRAL",
            raw_flight="not json",
        )
        self.assertGreater(self.node._logic.bad_flight_states, 0)

    def test_flight_state_with_a_string_boolean_goes_idle(self):
        text = json.dumps(dict(FLIGHT_OK, override_active="true"))
        self.feed(0.3, "NEUTRAL")
        self.wait(
            self.mode_is("IDLE", "override_off"), intent="NEUTRAL", raw_flight=text
        )

    def test_state_is_published_on_change_and_at_2_hz(self):
        self.feed(0.5, "NEUTRAL")
        start = len(self.states)
        self.feed(2.0, "NEUTRAL")
        count = len(self.states) - start
        self.assertTrue(3 <= count <= 6, f"{count} states in 2 s without a change")
        sent = time.monotonic()
        self.wait(self.mode_is("ENGAGED"), timeout=1.0, intent="HOLD_POSITION")
        received = next(
            t for t, s in self.states if t >= sent and s["mode"] == "ENGAGED"
        )
        self.assertLessEqual(received - sent, PUBLISH_PERIOD * 2 + TRANSPORT_SLACK_SEC)

    def test_state_has_every_documented_field(self):
        self.feed(0.3, "NEUTRAL")
        state = self.state()
        self.assertEqual(set(state), STATE_KEYS)
        self.assertEqual((state["mask"], state["airborne"]), (11, True))
        self.assertIsInstance(state["intent_age_ms"], int)

    def test_stop_output_publishes_a_zero_target(self):
        self.move_right()
        self.executor.remove_node(self.node)  # no more ticks: only stop_output
        self.node.stop_output()
        self.assertTrue(self.spin_until(lambda: self.vel() == ZERO, timeout=1.0))
        self.assertEqual(self.velocities[-1][2].header.frame_id, "base_link")

    def health_report(self):
        """Run the 5 s health report now; returns (level, text) of its log line."""
        logger = mock.Mock()
        with mock.patch.object(self.node, "get_logger", return_value=logger):
            self.node._report_health()
        for level in ("info", "warning"):
            log = getattr(logger, level)
            if log.called:
                return level, log.call_args.args[0]
        self.fail("the health report logged nothing")

    def test_health_report(self):
        level, text = self.health_report()  # nothing received yet
        self.assertEqual(level, "warning")
        self.assertIn("0 intents", text)
        self.feed(0.3, "NEUTRAL")
        self.feed(0.1, raw_intent="{")
        level, text = self.health_report()
        self.assertEqual(level, "info")
        self.assertIn(f"json {self.node._logic.bad_json}", text)
        self.assertGreater(self.node._logic.bad_json, 0)
        level, _ = self.health_report()  # the window starts again
        self.assertEqual(level, "warning")

    def test_logs_mode_changes_and_masked_axes(self):
        logger = mock.Mock()
        with mock.patch.object(self.node, "get_logger", return_value=logger):
            self.engage()
            self.feed(0.3, "CLIMB_SLOWLY")
            self.wait(
                self.mode_is("LAND_REQUEST"),
                intent="HOVER_LAND_REQUEST",
                reason="no_hand_long",
            )
            self.feed(0.3, raw_intent="{")
        infos = [c.args[0] for c in logger.info.call_args_list]
        warnings = [c.args[0] for c in logger.warning.call_args_list]
        self.assertIn("Mode -> ENGAGED (hold_position)", infos)
        self.assertIn("Not commanded, mask 11: ['axis_not_owned:z']", infos)
        self.assertIn("Mode -> LAND_REQUEST (no_hand_long)", warnings)
        self.assertTrue(
            any(w.startswith("Bad /gesture/intent (bad_json)") for w in warnings)
        )


class RemappedTopicTests(MissionProbe):
    params = dict(
        intent_topic="/test/intent",
        flight_state_topic="/test/flight",
        velocity_topic="/test/velocity",
        state_topic="/test/state",
        frame_id="body",
    )

    def test_uses_the_topics_and_frame_from_parameters(self):
        self.move_right()
        self.assertEqual(self.velocities[-1][2].header.frame_id, "body")


class SlowRateTests(MissionProbe):
    params = dict(publish_rate_hz=10.0)

    def test_publishes_at_the_set_rate(self):
        count = self.count_velocities(1.0, intent="NEUTRAL")
        self.assertTrue(7 <= count <= 13, f"{count} messages in 1 s at 10 Hz")

    # REQ-SAF-017 also holds when the output is slower than the input
    def test_stale_intent_still_gives_zero_within_0_25_s(self):
        self.move_right()
        last_sent = self.last_intent_sent
        start = len(self.velocities)
        self.wait(lambda: self.vel() == ZERO, timeout=1.0)
        first_zero = next(t for t, v, _ in self.velocities[start:] if v == ZERO)
        self.assertLessEqual(first_zero - last_sent, 0.25 + TRANSPORT_SLACK_SEC)


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class ParameterTests(RosTestCase):
    def make(self, **params):
        return self.add(MissionNode(parameter_overrides=overrides(params)))

    def test_speeds_above_the_ceiling_are_clamped_with_a_warning(self):
        logger = mock.Mock()
        with mock.patch.object(MissionNode, "get_logger", return_value=logger):
            node = self.make(max_lateral_mps=2.0, max_yaw_rate_dps=90.0)
        self.assertEqual(node._logic.max_lateral, 0.5)
        self.assertAlmostEqual(node._logic.max_yaw, math.radians(45.0))
        warnings = " ".join(c.args[0] for c in logger.warning.call_args_list)
        self.assertIn("max_lateral_mps", warnings)
        self.assertIn("max_yaw_rate_dps", warnings)

    def test_integers_are_accepted_for_decimal_parameters(self):
        node = self.make(intent_lost_sec=3, max_lateral_mps=0, publish_rate_hz=10)
        self.assertEqual(node._logic.intent_lost, 3.0)
        self.assertEqual(node._logic.max_lateral, 0.0)

    def test_values_that_are_not_numbers_are_rejected(self):
        for value in ("fast", True, [0.1, 0.2]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "max_lateral_mps"):
                    MissionNode(
                        parameter_overrides=overrides({"max_lateral_mps": value})
                    )


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class FakeFlightStateTests(RosTestCase):
    def setUp(self):
        super().setUp()
        self.fake = self.add(FakeFlightState())
        self.probe = self.add(rclpy.create_node("fake_flight_state_test_probe"))
        self.received = []
        self.probe.create_subscription(
            String, "/flight/state", lambda m: self.received.append(m.data), 10
        )
        self.assertTrue(self.spin_until(lambda: self.received), "nothing received")

    def set(self, **params):
        self.fake.set_parameters(overrides(params))

    def test_publishes_every_field_at_10_hz(self):
        start = len(self.received)
        self.spin_for(1.0)
        count = len(self.received) - start
        self.assertTrue(7 <= count <= 13, f"{count} messages in 1 s")
        self.assertEqual(json.loads(self.received[-1]), FLIGHT_OK)

    def test_fields_change_while_running(self):
        self.set(mask=15, airborne=False, esp_state="FAILSAFE")
        self.assertTrue(
            self.spin_until(
                lambda: json.loads(self.received[-1])
                == dict(FLIGHT_OK, mask=15, airborne=False, esp_state="FAILSAFE")
            )
        )

    def test_publish_false_goes_silent_until_true(self):
        self.set(publish=False)
        self.spin_for(0.2)  # drain what was on the way
        count = len(self.received)
        self.spin_for(0.5)
        self.assertEqual(len(self.received), count)
        self.set(publish=True)
        self.assertTrue(self.spin_until(lambda: len(self.received) > count))

    def test_raw_text_is_sent_as_is(self):
        self.set(raw="not json")
        self.assertTrue(self.spin_until(lambda: self.received[-1] == "not json"))
        self.set(raw="")
        self.assertTrue(
            self.spin_until(
                lambda: self.received[-1] != "not json"
                and json.loads(self.received[-1]) == FLIGHT_OK
            )
        )


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class FakeIntentTests(RosTestCase):
    def start(self, **params):
        self.fake = self.add(FakeIntent(parameter_overrides=overrides(params)))
        self.probe = self.add(rclpy.create_node("fake_intent_test_probe"))
        self.received = []
        self.probe.create_subscription(
            String,
            "/gesture/intent",
            lambda m: self.received.append(json.loads(m.data)),
            10,
        )
        self.assertTrue(self.spin_until(lambda: self.received), "nothing received")

    def test_constant_mode_publishes_like_intent_node_at_20_hz(self):
        self.start(mode="constant", intent="MOVE_LEFT")
        start = len(self.received)
        self.spin_for(1.0)
        count = len(self.received) - start
        self.assertTrue(14 <= count <= 26, f"{count} messages in 1 s")
        last = self.received[-1]
        self.assertEqual(set(last), INTENT_KEYS)
        self.assertEqual(
            (last["intent"], last["gesture"], last["reason"]),
            ("MOVE_LEFT", "INDEX_LEFT", "ok"),
        )

    def test_constant_intent_and_reason_change_while_running(self):
        self.start(mode="constant")
        self.fake.set_parameters(
            overrides({"intent": "HOVER_LAND_REQUEST", "reason": "no_hand_long"})
        )
        self.assertTrue(
            self.spin_until(
                lambda: (self.received[-1]["intent"], self.received[-1]["reason"])
                == ("HOVER_LAND_REQUEST", "no_hand_long")
            )
        )

    def test_demo_starts_with_no_hand(self):
        self.start()
        self.assertEqual(
            (self.received[0]["intent"], self.received[0]["reason"]),
            ("NEUTRAL", "no_hand"),
        )

    def test_demo_silence_step_publishes_nothing(self):
        self.start()
        self.fake._start -= script_duration() - 2.0  # into the last step: silence
        self.spin_for(0.2)
        count = len(self.received)
        self.spin_for(0.5)
        self.assertEqual(len(self.received), count)

    def test_demo_finishes_and_stops(self):
        self.start()
        self.fake._start -= script_duration()
        self.assertTrue(self.spin_until(lambda: self.fake.finished, timeout=1.0))

    def test_demo_loop_starts_again(self):
        self.start(loop=True)
        self.fake._start -= script_duration()
        self.spin_for(0.3)
        self.assertFalse(self.fake.finished)
        self.assertEqual(self.received[-1]["reason"], "no_hand")  # step 1 again


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class FakeInputsDriveMissionTests(Recorder, RosTestCase):
    """The real MissionNode fed by fake_flight_state and fake_intent (constant
    mode), the way mission_demo.launch.py runs them, through every fault the
    pretend flight state can produce."""

    FAULTS = [
        (dict(override_active=False), "override_off"),
        (dict(link_ok=False), "link_lost"),
        (dict(esp_state="FAILSAFE"), "not_streaming"),
        (dict(mask=7), "bad_mask"),
        (dict(publish=False), "flight_state_stale"),
        (dict(raw="not json"), "flight_state_stale"),
        (dict(raw=json.dumps(dict(FLIGHT_OK, link_ok="true"))), "link_lost"),
        (dict(raw=json.dumps(dict(FLIGHT_OK, mask=11.0))), "bad_mask"),
    ]
    DEFAULTS = dict(
        override_active=True,
        link_ok=True,
        esp_state="STREAMING",
        mask=11,
        airborne=True,
        publish=True,
        raw="",
    )

    def setUp(self):
        super().setUp()
        self.mission = self.add(MissionNode())
        self.flight = self.add(FakeFlightState())
        self.intent = self.add(
            FakeIntent(parameter_overrides=overrides({"mode": "constant"}))
        )
        self.probe = self.add(rclpy.create_node("fake_inputs_test_probe"))
        self.record_outputs(self.probe, "/mission/velocity_target", "/mission/state")
        self.assertTrue(self.spin_until(self.mode_is("READY")), self.state())

    def show(self, intent, reason="ok"):
        self.intent.set_parameters(overrides({"intent": intent, "reason": reason}))

    def wait(self, condition, timeout=3.0):
        self.assertTrue(
            self.spin_until(condition, timeout),
            f"last state {self.state()}, velocity {self.vel()}",
        )

    def move_right(self):
        self.show("NEUTRAL")
        self.spin_for(0.3)
        self.show("HOLD_POSITION")
        self.wait(self.mode_is("ENGAGED"))
        self.show("MOVE_RIGHT")
        self.wait(lambda: self.vel()[1] <= -0.3 + 1e-9)

    def test_every_pretend_fault_stops_and_needs_a_new_engage(self):
        for fault, reason in self.FAULTS:
            with self.subTest(fault=fault):
                self.move_right()
                self.flight.set_parameters(overrides(fault))
                self.wait(lambda: self.vel() == ZERO, timeout=1.5)
                self.wait(self.mode_is("IDLE", reason), timeout=1.0)
                self.flight.set_parameters(overrides(self.DEFAULTS))
                self.wait(self.mode_is("READY"), timeout=1.5)
                start = len(self.velocities)
                self.spin_for(0.5)  # MOVE_RIGHT still shown: no motion
                self.assertTrue(all(v == ZERO for _, v, _ in self.velocities[start:]))

    def test_mask_from_the_pretend_flight_state(self):
        self.flight.set_parameters(overrides({"mask": 15}))
        self.spin_for(0.3)
        self.show("NEUTRAL")
        self.spin_for(0.3)
        self.show("HOLD_POSITION")
        self.wait(self.mode_is("ENGAGED"))
        self.show("CLIMB_SLOWLY")
        self.wait(lambda: abs(self.vel()[2] - 0.2) < 1e-9)
        self.flight.set_parameters(overrides({"mask": 11}))
        self.wait(lambda: self.vel()[2] == 0.0, timeout=1.0)

    def test_land_request_reasons_from_fake_intent(self):
        self.show("HOVER_LAND_REQUEST", "no_packets_yet")  # intent_node start-up
        self.spin_for(0.5)
        self.assertEqual(self.state()["mode"], "READY")
        self.show("HOVER_LAND_REQUEST", "no_hand_long")
        self.wait(self.mode_is("LAND_REQUEST", "no_hand_long"))
        self.flight.set_parameters(overrides({"airborne": False}))
        self.wait(self.mode_is("READY", "landed"))


if __name__ == "__main__":
    unittest.main()
