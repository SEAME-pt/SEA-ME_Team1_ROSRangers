#!/usr/bin/env python3
"""controller_node (#199): /mission/velocity_target + /estimate/velocity ->
/control/attitude_setpoint. The control law is in controller_logic.py.

Single-threaded: one timer runs the logic and publishes, the subscriptions
only store the latest message. No threads, no locks.

Output: JSON on std_msgs/String (ADR-025 C1.1, F17), published at rate_hz
whether active or not, so flight_interface and the live table see the node
alive:
    {"roll": 0.0, "pitch": 5.8, "yaw_rate": 0.0, "throttle": 0.71,
     "active": true, "reason": "ok"}
Freshness is measured on receive (monotonic clock), like mission_node and
the flight_interface guard, not from the message stamps.

`mask` and `hover_throttle` must equal flight_interface's (F15): the mode
file repeats them in both sections and test_config.py checks they agree.
hover_throttle 0.0 = not measured = always inactive (F16).

Ctrl+C and SIGTERM (docker stop) are a normal stop: one last inactive
setpoint, then exit code 0. A bad parameter: FATAL log and exit code 1.
"""

import json
import time

from geometry_msgs.msg import TwistStamped
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from std_msgs.msg import String

from controller_node.controller_logic import (
    HOVER_NOT_MEASURED,
    MAX_INPUT_TIMEOUT,
    MAX_THROTTLE_BAND,
    MAX_TILT_CAP_DEG,
    STOPPED,
    Controller,
    Gains,
    Params,
)
from controller_node.spin import run

HEALTH_REPORT_SEC = 5.0

# Number parameters and their defaults. An integer (`-p kp_xy:=1`) is
# accepted as well; anything else stops the node at start-up.
NUMBER_PARAMS = {
    "rate_hz": 50.0,  # flight_interface's SETPOINT rate
    "hover_throttle": HOVER_NOT_MEASURED,  # F16; the same as flight_interface's (F15)
    "tilt_cap_deg": MAX_TILT_CAP_DEG,
    "throttle_band": MAX_THROTTLE_BAND,
    "target_timeout_sec": MAX_INPUT_TIMEOUT,
    "velocity_timeout_sec": MAX_INPUT_TIMEOUT,
    "kp_xy": Gains.kp_xy,
    "ki_xy": Gains.ki_xy,
    "kp_z": Gains.kp_z,
    "ki_z": Gains.ki_z,
}


class ControllerNode(Node):
    def __init__(self, **kwargs):
        super().__init__("controller_node", **kwargs)

        self.declare_parameter("target_topic", "/mission/velocity_target")
        self.declare_parameter("velocity_topic", "/estimate/velocity")
        self.declare_parameter("setpoint_topic", "/control/attitude_setpoint")
        self.declare_parameter("mask", 8)  # yaw only, as flight_interface; F15
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

        rate = number("rate_hz")
        if not 1.0 <= rate <= 100.0:
            raise ValueError(f"Parameter rate_hz must be in [1, 100], got {rate}")
        gains = Gains(
            kp_xy=number("kp_xy"),
            ki_xy=number("ki_xy"),
            kp_z=number("kp_z"),
            ki_z=number("ki_z"),
        )
        params = Params(
            hover_throttle=number("hover_throttle"),
            mask=param("mask"),
            tilt_cap_deg=number("tilt_cap_deg"),
            throttle_band=number("throttle_band"),
            target_timeout=number("target_timeout_sec"),
            velocity_timeout=number("velocity_timeout_sec"),
            gains=gains,
        )
        # Params.validate() raises ValueError: FATAL and exit 1 (spin.run).
        self._logic = Controller(params)

        target_topic = str(param("target_topic"))
        velocity_topic = str(param("velocity_topic"))
        setpoint_topic = str(param("setpoint_topic"))
        self.create_subscription(TwistStamped, target_topic, self._on_target, 10)
        self.create_subscription(TwistStamped, velocity_topic, self._on_velocity, 10)
        self._pub = self.create_publisher(String, setpoint_topic, 10)

        self._target_window = 0
        self._velocity_window = 0
        self._last_reason = None
        self.create_timer(1.0 / rate, self._tick)
        self.create_timer(HEALTH_REPORT_SEC, self._report_health)

        self.get_logger().info(
            f"Reading {target_topic} and {velocity_topic}; publishing "
            f"{setpoint_topic} at {rate:.0f} Hz; mask {params.mask}, hover "
            f"throttle {params.hover_throttle}"
        )

    # ---------- Subscriptions: store only ----------
    def _on_target(self, msg):
        self._target_window += 1
        t = msg.twist
        self._logic.on_target(
            t.linear.x, t.linear.y, t.linear.z, t.angular.z, time.monotonic()
        )

    def _on_velocity(self, msg):
        self._velocity_window += 1
        t = msg.twist
        self._logic.on_velocity(t.linear.x, t.linear.y, t.linear.z, time.monotonic())

    # ---------- tick ----------
    def _publish(self, setpoint):
        msg = String()
        msg.data = json.dumps(setpoint)
        self._pub.publish(msg)

    def _tick(self):
        setpoint = self._logic.step(time.monotonic())
        self._publish(setpoint)
        reason = setpoint["reason"]
        if reason != self._last_reason:
            self.get_logger().info(f"active {setpoint['active']}, reason {reason}")
            self._last_reason = reason

    def _report_health(self):
        text = (
            f"Health: {self._target_window} targets and {self._velocity_window} "
            f"measured velocities in the last {HEALTH_REPORT_SEC:.0f} s; "
            f"reason {self._last_reason}"
        )
        if self._target_window == 0 or self._velocity_window == 0:
            self.get_logger().warning(text)
        else:
            self.get_logger().info(text)
        self._target_window = 0
        self._velocity_window = 0

    def stop_output(self):
        """Publish one inactive setpoint on the way out (F8), so nobody keeps
        the last one."""
        self._publish(self._logic.neutral(STOPPED))


def main(args=None):
    # Ctrl+C and SIGTERM (docker stop) end the loop; the final neutral goes
    # out while the context is still alive.
    run(ControllerNode, on_stop=ControllerNode.stop_output, args=args)


if __name__ == "__main__":
    main()
