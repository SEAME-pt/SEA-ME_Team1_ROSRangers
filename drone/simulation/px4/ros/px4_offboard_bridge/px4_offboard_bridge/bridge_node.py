#!/usr/bin/env python3
"""px4_offboard_bridge: /mission/velocity_target -> PX4 offboard velocity (SIM ONLY, #86).

Stand-in for the PX4 backend of flight_interface (ADR-019), so the ground
chain can be tried against PX4 SITL + Gazebo. NEVER use it with the real drone.

    /mission/velocity_target (TwistStamped, body FLU)
        -> /fmu/in/offboard_control_mode + /fmu/in/trajectory_setpoint (NED)
    /fmu/out/vehicle_status_v1, vehicle_local_position_v1, vehicle_land_detected
        -> /flight/state (JSON, the fields mission_node reads)

The bridge streams setpoints all the time (PX4 needs them before and during
Offboard), sends zero when the target is older than target_timeout_sec, and
zeroes the axes outside the mask. It never arms, disarms or changes mode:
that is the scenario's job (the safety pilot's, on the real drone).
"""

import json
import math
import time

from geometry_msgs.msg import TwistStamped
from mission_node.spin import run
from px4_msgs.msg import (
    OffboardControlMode,
    TrajectorySetpoint,
    VehicleLandDetected,
    VehicleLocalPosition,
    VehicleStatus,
)
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from px4_offboard_bridge.bridge_logic import ZERO, BridgeLogic

NAN = math.nan


class Px4OffboardBridge(Node):
    def __init__(self):
        super().__init__("px4_offboard_bridge")
        self.declare_parameter("velocity_topic", "/mission/velocity_target")
        self.declare_parameter("flight_state_topic", "/flight/state")
        self.declare_parameter("mask", 15)
        # Dynamic typing: rate_hz:=20 (an integer) is accepted as well as 20.0.
        any_type = ParameterDescriptor(dynamic_typing=True)
        self.declare_parameter("rate_hz", 20.0, any_type)
        self.declare_parameter("target_timeout_sec", 0.25, any_type)

        mask = int(self.get_parameter("mask").value)
        rate = float(self.get_parameter("rate_hz").value)
        rate = min(50.0, max(10.0, rate)) if math.isfinite(rate) else 20.0
        # BridgeLogic raises ValueError for a bad mask: run() logs FATAL, exit 1.
        self._logic = BridgeLogic(
            mask=mask,
            target_timeout=float(self.get_parameter("target_timeout_sec").value),
            tick_period=1.0 / rate,
        )

        # PX4 uXRCE-DDS topics: best effort, as in px4_ros_com's offboard example.
        px4_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._mode_pub = self.create_publisher(
            OffboardControlMode, "/fmu/in/offboard_control_mode", px4_qos
        )
        self._sp_pub = self.create_publisher(
            TrajectorySetpoint, "/fmu/in/trajectory_setpoint", px4_qos
        )
        self.create_subscription(
            VehicleStatus, "/fmu/out/vehicle_status_v1", self._on_status, px4_qos
        )
        self.create_subscription(
            VehicleLocalPosition,
            "/fmu/out/vehicle_local_position_v1",
            self._on_position,
            px4_qos,
        )
        self.create_subscription(
            VehicleLandDetected,
            "/fmu/out/vehicle_land_detected",
            self._on_land,
            px4_qos,
        )

        velocity_topic = str(self.get_parameter("velocity_topic").value)
        state_topic = str(self.get_parameter("flight_state_topic").value)
        self.create_subscription(TwistStamped, velocity_topic, self._on_target, 10)
        self._state_pub = self.create_publisher(String, state_topic, 10)

        self.create_timer(1.0 / rate, self._tick)
        self.get_logger().warning(
            f"SIMULATION ONLY: {velocity_topic} -> PX4 offboard velocity at "
            f"{rate:.0f} Hz, mask {mask}, zero after "
            f"{self._logic.target_timeout:.2f} s. Never use with the real drone."
        )

    @staticmethod
    def _now():
        # Freshness uses the monotonic clock: a wall-clock step (NTP) cannot make
        # an old target look fresh, and a paused sim clock cannot freeze it.
        return time.monotonic()

    def _now_us(self):
        # PX4 message timestamps, in µs, as in px4_ros_com's offboard example.
        return self.get_clock().now().nanoseconds // 1000

    # ---------- inputs ----------
    def _on_target(self, msg):
        t = msg.twist
        self._logic.on_target(
            t.linear.x, t.linear.y, t.linear.z, t.angular.z, self._now()
        )

    def _on_status(self, msg):
        self._logic.on_status(
            msg.arming_state == VehicleStatus.ARMING_STATE_ARMED,
            msg.nav_state == VehicleStatus.NAVIGATION_STATE_OFFBOARD,
            self._now(),
        )

    def _on_position(self, msg):
        self._logic.on_position(msg.heading, msg.heading_good_for_control, self._now())

    def _on_land(self, msg):
        self._logic.on_landed(msg.landed)

    # ---------- 20 Hz output ----------
    def _tick(self):
        now = self._now()
        self._publish_setpoint(self._logic.setpoint(now))
        state = self._logic.flight_state(now)
        self._state_pub.publish(String(data=json.dumps(state, separators=(",", ":"))))

    def _publish_setpoint(self, ned):
        stamp = self._now_us()
        mode = OffboardControlMode()
        mode.timestamp = stamp
        mode.position = False
        mode.velocity = True
        mode.acceleration = False
        mode.attitude = False
        mode.body_rate = False
        self._mode_pub.publish(mode)

        v_north, v_east, v_down, yawspeed = ned
        sp = TrajectorySetpoint()
        sp.timestamp = stamp
        sp.position = [NAN, NAN, NAN]
        sp.velocity = [float(v_north), float(v_east), float(v_down)]
        sp.acceleration = [NAN, NAN, NAN]
        sp.jerk = [NAN, NAN, NAN]
        sp.yaw = NAN
        sp.yawspeed = float(yawspeed)
        self._sp_pub.publish(sp)

    def stop_output(self):
        """One zero setpoint on the way out, so PX4 does not keep the last one
        until its offboard-loss timeout."""
        self._publish_setpoint(ZERO)


def main(args=None):
    # Same run loop as mission_node: Ctrl+C / SIGTERM stop the spin, the final
    # zero goes out while the node can still publish, a bad parameter is FATAL.
    run(Px4OffboardBridge, on_stop=Px4OffboardBridge.stop_output, args=args)


if __name__ == "__main__":
    main()
