#!/usr/bin/env python3
"""mission_node: /gesture/intent + /flight/state -> /mission/velocity_target.

Single-threaded: one timer runs the logic and publishes, the subscriptions
only store the latest message. No threads, no locks.

Ctrl+C and SIGTERM (docker stop) are a normal stop: one last zero target,
then exit code 0. A parameter that is not a number: FATAL log and exit code 1.
"""

import json

from geometry_msgs.msg import TwistStamped
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from std_msgs.msg import String

from mission_node.mission_logic import MissionLogic
from mission_node.spin import run

HEALTH_REPORT_SEC = 5.0
STATE_REPEAT_SEC = 0.5  # /mission/state at 2 Hz when nothing changes

# Number parameters and their defaults. An integer (`-p intent_lost_sec:=3`)
# is accepted as well; anything else stops the node at start-up.
NUMBER_PARAMS = {
    "intent_timeout_sec": 0.25,
    "intent_lost_sec": 2.0,
    "flight_state_timeout_sec": 0.5,
    "max_lateral_mps": 0.3,
    "max_vertical_mps": 0.2,
    "max_yaw_rate_dps": 20.0,
    "max_accel_mps2": 0.5,
    "max_yaw_accel_dps2": 40.0,
    "land_hover_sec": 2.0,
    "publish_rate_hz": 20.0,
}


class MissionNode(Node):
    def __init__(self, **kwargs):
        super().__init__("mission_node", **kwargs)

        self.declare_parameter("intent_topic", "/gesture/intent")
        self.declare_parameter("flight_state_topic", "/flight/state")
        self.declare_parameter("velocity_topic", "/mission/velocity_target")
        self.declare_parameter("state_topic", "/mission/state")
        self.declare_parameter("frame_id", "base_link")
        # Dynamic typing lets an integer through; number() checks the type.
        any_type = ParameterDescriptor(dynamic_typing=True)
        for name, default in NUMBER_PARAMS.items():
            self.declare_parameter(name, default, any_type)

        def param(name):
            return self.get_parameter(name).value

        def number(name):
            value = param(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Parameter {name} must be a number, got {value!r}")
            return float(value)

        rate = max(1.0, number("publish_rate_hz"))
        self._logic = MissionLogic(
            intent_timeout=number("intent_timeout_sec"),
            intent_lost=number("intent_lost_sec"),
            flight_state_timeout=number("flight_state_timeout_sec"),
            max_lateral_mps=number("max_lateral_mps"),
            max_vertical_mps=number("max_vertical_mps"),
            max_yaw_rate_dps=number("max_yaw_rate_dps"),
            max_accel_mps2=number("max_accel_mps2"),
            max_yaw_accel_dps2=number("max_yaw_accel_dps2"),
            land_hover_sec=number("land_hover_sec"),
            tick_period=1.0 / rate,
        )
        for name in self._logic.clamped_params:
            self.get_logger().warning(
                f"Parameter {name} is above the hard ceiling and was clamped"
            )

        self._frame_id = str(param("frame_id"))
        intent_topic = str(param("intent_topic"))
        flight_topic = str(param("flight_state_topic"))
        velocity_topic = str(param("velocity_topic"))
        state_topic = str(param("state_topic"))

        self.create_subscription(String, intent_topic, self._on_intent, 10)
        self.create_subscription(String, flight_topic, self._on_flight_state, 10)
        self._vel_pub = self.create_publisher(TwistStamped, velocity_topic, 10)
        self._state_pub = self.create_publisher(String, state_topic, 10)

        self._intent_window = 0
        self._flight_window = 0
        self._last_state = None
        self._last_signature = None
        self._last_state_pub = None
        self._last_mode = None

        self.create_timer(1.0 / rate, self._tick)
        self.create_timer(HEALTH_REPORT_SEC, self._report_health)

        self.get_logger().info(
            f"Reading {intent_topic} and {flight_topic}; publishing "
            f"{velocity_topic} at {rate:.0f} Hz and {state_topic}"
        )

    # ---------- Subscriptions: store only ----------
    def _on_intent(self, msg):
        self._intent_window += 1
        problem = self._logic.on_intent(msg.data)
        if problem is not None:
            self.get_logger().warning(
                f"Bad /gesture/intent ({problem}) treated as NEUTRAL: {msg.data!r}",
                throttle_duration_sec=2.0,
            )

    def _on_flight_state(self, msg):
        self._flight_window += 1
        if not self._logic.on_flight_state(msg.data):
            self.get_logger().warning(
                f"Ignored a bad /flight/state message: {msg.data!r}",
                throttle_duration_sec=2.0,
            )

    # ---------- 20 Hz tick ----------
    def _tick(self):
        result = self._logic.tick()
        vx, vy, vz, wz = result["velocity"]
        state = result["state"]
        self._last_state = state

        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._frame_id
        msg.twist.linear.x = vx
        msg.twist.linear.y = vy
        msg.twist.linear.z = vz
        msg.twist.angular.z = wz
        self._vel_pub.publish(msg)

        # /mission/state on every change, and at 2 Hz otherwise.
        signature = {k: v for k, v in state.items() if k != "intent_age_ms"}
        now = self.get_clock().now().nanoseconds * 1e-9
        if (
            signature != self._last_signature
            or self._last_state_pub is None
            or now - self._last_state_pub >= STATE_REPEAT_SEC
        ):
            out = String()
            out.data = json.dumps(state, separators=(",", ":"))
            self._state_pub.publish(out)
            self._last_state_pub = now
        self._log_changes(state, signature)

    def _log_changes(self, state, signature):
        old = self._last_signature or {}
        self._last_signature = signature
        if state["mode"] != self._last_mode:
            self._last_mode = state["mode"]
            text = f"Mode -> {state['mode']} ({state['mode_reason']})"
            # rclpy needs one severity per call site, so two separate calls.
            if state["land_request"]:
                self.get_logger().warning(text)
            else:
                self.get_logger().info(text)
        if (state["intent"], state["reason"]) != (old.get("intent"), old.get("reason")):
            self.get_logger().info(
                f"Intent -> {state['intent']} (reason: {state['reason']})"
            )
        new_limits = {s for s in state["limited"] if s.startswith("axis_not_owned")}
        old_limits = {s for s in old.get("limited", []) if s.startswith("axis_not")}
        if new_limits - old_limits:
            self.get_logger().info(
                f"Not commanded, mask {state['mask']}: {sorted(new_limits)}"
            )

    def _report_health(self):
        state = self._last_state or {}
        logic = self._logic
        text = (
            f"Health: {self._intent_window} intents and {self._flight_window} "
            f"flight states in the last {HEALTH_REPORT_SEC:.0f} s; mode "
            f"{state.get('mode')}, last intent {state.get('intent')}; bad: "
            f"json {logic.bad_json}, unknown intents {logic.unknown_intents}, "
            f"flight states {logic.bad_flight_states}"
        )
        if self._intent_window == 0 or self._flight_window == 0:
            self.get_logger().warning(text)
        else:
            self.get_logger().info(text)
        self._intent_window = 0
        self._flight_window = 0

    def stop_output(self):
        """Publish one zero target on the way out, so nobody keeps the last one."""
        msg = TwistStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._frame_id
        self._vel_pub.publish(msg)


def main(args=None):
    # Ctrl+C and SIGTERM (docker stop) end the loop; the final zero goes out
    # while the context is still alive.
    run(MissionNode, on_stop=MissionNode.stop_output, args=args)


if __name__ == "__main__":
    main()
