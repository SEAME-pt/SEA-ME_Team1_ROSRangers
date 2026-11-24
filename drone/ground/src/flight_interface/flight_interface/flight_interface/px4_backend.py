"""px4 backend of flight_interface (#199, ADR-025 F3, F13, F19). SIMULATION
ONLY: PX4 SITL + Gazebo through the Micro XRCE-DDS agent. Never the real drone.

The ROS side of px4_logic.py (read that first). It needs the node to create
its publishers and subscriptions, and px4_msgs, which only the simulation
image has: make_backend() imports this module only for backend:=px4.

Out, every tick (PX4 needs the stream before and during Offboard):
  * a commanding setpoint: OffboardControlMode(attitude) +
    VehicleAttitudeSetpoint (q_d, thrust_body, yaw_sp_move_rate);
  * neutral (F19): OffboardControlMode(velocity) + TrajectorySetpoint with a
    zero velocity, so PX4 holds position itself.
In: vehicle_status, vehicle_local_position, vehicle_land_detected -> the
drone state for the guard (poll()), and /estimate/velocity (F1) from each
local position whose velocity PX4 calls valid. Its vertical part is z_deriv,
the rate of PX4's height estimate, not vz: in SITL vz carries a bias of about
0.1 m/s against that height, and the controller, holding vz at zero, let the
drone climb about 6 m a minute (ADR-025 F1 note, 2026-11-24).

Never arms, disarms or changes PX4's mode (REQ-SAF-023): arm_control() and
request_resume() send nothing. end_session() (the E-stop) only stops: the node
sends nothing more and PX4's own Offboard-loss failsafe takes over.
"""

import math
import time

from geometry_msgs.msg import TwistStamped
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from flight_interface.backends import Backend
from flight_interface.px4_logic import Px4Logic, ned_to_flu

NAN = math.nan
# PX4 v1.17's uXRCE-DDS topics; versioned messages get a _v<N> suffix.
OFFBOARD_MODE_TOPIC = "/fmu/in/offboard_control_mode"
ATTITUDE_SETPOINT_TOPIC = "/fmu/in/vehicle_attitude_setpoint_v1"
TRAJECTORY_SETPOINT_TOPIC = "/fmu/in/trajectory_setpoint"
STATUS_TOPIC = "/fmu/out/vehicle_status_v1"
POSITION_TOPIC = "/fmu/out/vehicle_local_position_v1"
LAND_TOPIC = "/fmu/out/vehicle_land_detected"
VELOCITY_TOPIC = "/estimate/velocity"  # F1, read by controller_node


def px4_qos():
    """PX4 uXRCE-DDS topics: best effort, as in px4_ros_com's offboard example."""
    return QoSProfile(
        reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
    )


def load_px4_msgs():
    """The px4_msgs message classes. ImportError outside the simulation image,
    which configure reports as a failure."""
    from px4_msgs import msg

    return msg


class Px4Backend(Backend):
    name = "px4"

    def __init__(self, node, mask, hover_throttle, msgs=None, clock=time.monotonic):
        # Neutral is PX4's own hold (F19), but a commanding setpoint carries the
        # hover throttle on the axes the ground does not own (F2): 0.0 would be
        # thrust 0 there. Refused before anything is created.
        if not (isinstance(hover_throttle, (int, float)) and hover_throttle > 0.0):
            raise ValueError(
                "backend px4 needs a measured hover_throttle (sim.yaml), got "
                f"{hover_throttle!r}"
            )
        self._node = node
        self._msgs = msgs if msgs is not None else load_px4_msgs()
        self._clock = clock
        self.logic = Px4Logic(mask)
        qos = px4_qos()
        m = self._msgs
        self._mode_pub = node.create_publisher(
            m.OffboardControlMode, OFFBOARD_MODE_TOPIC, qos
        )
        self._attitude_pub = node.create_publisher(
            m.VehicleAttitudeSetpoint, ATTITUDE_SETPOINT_TOPIC, qos
        )
        self._trajectory_pub = node.create_publisher(
            m.TrajectorySetpoint, TRAJECTORY_SETPOINT_TOPIC, qos
        )
        self._velocity_pub = node.create_publisher(TwistStamped, VELOCITY_TOPIC, 10)
        self._subs = [
            node.create_subscription(
                m.VehicleStatus, STATUS_TOPIC, self._on_status, qos
            ),
            node.create_subscription(
                m.VehicleLocalPosition, POSITION_TOPIC, self._on_position, qos
            ),
            node.create_subscription(
                m.VehicleLandDetected, LAND_TOPIC, self._on_land, qos
            ),
        ]
        self.sent_attitude = 0  # counters for the tests and the log
        self.sent_neutral = 0

    # ---------- PX4 -> here ----------
    def _on_status(self, msg):
        m = self._msgs.VehicleStatus
        armed = msg.arming_state == m.ARMING_STATE_ARMED
        offboard = msg.nav_state == m.NAVIGATION_STATE_OFFBOARD
        self.logic.on_status(armed, offboard, self._clock())

    def _on_position(self, msg):
        self.logic.on_position(msg.heading, msg.heading_good_for_control, self._clock())
        # z_deriv (down, m/s) is the rate of z: valid with z and vz
        velocity_valid = msg.v_xy_valid and msg.v_z_valid and msg.z_valid
        if not (velocity_valid and msg.heading_good_for_control):
            return
        values = (msg.vx, msg.vy, msg.z_deriv, msg.heading)
        if not all(math.isfinite(v) for v in values):
            return
        forward, left, up = ned_to_flu(msg.vx, msg.vy, msg.z_deriv, msg.heading)
        out = TwistStamped()
        out.header.stamp = self._node.get_clock().now().to_msg()
        out.header.frame_id = "base_link"
        out.twist.linear.x = forward
        out.twist.linear.y = left
        out.twist.linear.z = up
        self._velocity_pub.publish(out)

    def _on_land(self, msg):
        self.logic.on_landed(msg.landed, self._clock())

    def _timestamp_us(self):
        # PX4 message timestamps in µs, as px4_ros_com's offboard example
        return self._node.get_clock().now().nanoseconds // 1000

    def _mode(self, attitude):
        mode = self._msgs.OffboardControlMode()
        mode.timestamp = self._timestamp_us()
        mode.position = False
        mode.velocity = not attitude
        mode.acceleration = False
        mode.attitude = attitude
        mode.body_rate = False
        mode.thrust_and_torque = False
        mode.direct_actuator = False
        self._mode_pub.publish(mode)

    # ---------- the Backend interface ----------
    def send_setpoint(self, command, now):
        if not command.active:
            return self.send_neutral(now)
        attitude = self.logic.attitude(command, now)
        if attitude is None:  # no fresh heading
            return self.send_neutral(now)
        q_d, thrust_body, yaw_rate = attitude
        self._mode(attitude=True)
        sp = self._msgs.VehicleAttitudeSetpoint()
        sp.timestamp = self._timestamp_us()
        sp.q_d = [float(v) for v in q_d]
        sp.thrust_body = [float(v) for v in thrust_body]
        sp.yaw_sp_move_rate = float(yaw_rate)
        self._attitude_pub.publish(sp)
        self.sent_attitude += 1
        return True

    def send_neutral(self, now):
        """F19: zero velocity, PX4 holds position itself."""
        self.logic.reset_yaw()
        self._mode(attitude=False)
        sp = self._msgs.TrajectorySetpoint()
        sp.timestamp = self._timestamp_us()
        sp.position = [NAN, NAN, NAN]
        sp.velocity = [0.0, 0.0, 0.0]
        sp.acceleration = [NAN, NAN, NAN]
        sp.jerk = [NAN, NAN, NAN]
        sp.yaw = NAN
        sp.yawspeed = 0.0
        self._trajectory_pub.publish(sp)
        self.sent_neutral += 1
        return True

    def end_session(self, now):
        """E-stop: nothing to close. The node sends nothing more, and PX4's
        Offboard-loss failsafe takes over (it never changes PX4's mode here)."""
        self.logic.reset_yaw()
        return True

    def request_resume(self, now, session):
        self._node.get_logger().warning(
            "px4 backend: RESUME has no meaning here, nothing sent"
        )
        return False

    def arm_control(self, mask, now, session):
        # F3: the simulation script arms PX4 and switches it to Offboard.
        self._node.get_logger().warning(
            "px4 backend: arm_control sends nothing (sim_start.sh arms PX4)"
        )
        return False

    def poll(self, now):
        # `now` and the receive times are both time.monotonic() (the node's)
        state = self.logic.drone_state(now)
        if state is None:
            return []
        return [state]

    def close(self):
        node = self._node
        for sub in self._subs:
            node.destroy_subscription(sub)
        for pub in (
            self._mode_pub,
            self._attitude_pub,
            self._trajectory_pub,
            self._velocity_pub,
        ):
            node.destroy_publisher(pub)
        self._subs = []
