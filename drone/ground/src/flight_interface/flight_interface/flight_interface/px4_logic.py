"""Logic of the px4 backend (#199, SIMULATION ONLY), with NO ROS imports.

flight_interface's px4 backend (px4_backend.py) sends the guard's attitude
setpoints to PX4 SITL and turns PX4's state into the drone state the guard
reads. Everything that needs no ROS is here, so it is tested anywhere.

Frames (ADR-025 C1.1 note):
  * Our setpoint is body FLU, REP-103: roll + = right side down, pitch + =
    nose down, yaw_rate + = turn left (counter-clockwise from above).
  * PX4 is body FRD / earth NED: roll + = right side down (the same), pitch +
    = nose UP (the opposite), yaw + = clockwise from north (the opposite).
  So: roll stays, pitch and the yaw rate change sign.
    Example: pitch 5 deg in FLU (nose down, to go forward) = -5 deg in FRD.

Attitude (VehicleAttitudeSetpoint v1, PX4 v1.17): only q_d (quaternion,
body FRD -> NED), thrust_body (FRD, normalised, -z is up) and
yaw_sp_move_rate (a yaw-rate feed-forward, rad/s, FRD). We send a yaw RATE,
so the backend keeps a yaw setpoint: the current heading when control starts,
then moved by yaw_rate * dt each tick, never more than YAW_LEAD_CAP ahead of
or behind the real heading. A yaw rate of 0 holds the heading. Every neutral
resets it, so the next command starts from where the drone points.

Neutral (F19): PX4 holds position itself (velocity Offboard, zero velocity),
the backend's send_neutral(); nothing here.

Drone state for the guard (F13), with px4_offboard_bridge's rules:
  * fresh while PX4's status (STATUS_TIMEOUT) and local position
    (POSITION_TIMEOUT) are fresh; otherwise there is no state at all, so the
    guard's watchdog sees the link lost;
  * esp_state STREAMING and fc_link_ok true while fresh (there is no ESP);
  * override_active = armed and in Offboard (the sim's takeover switch);
  * mask = the configured one; armed from PX4; airborne = a land-detector
    message within LAND_TIMEOUT that says not landed (unknown is False here:
    display only, mission_node gates nothing on it).

Measured velocity (F1): PX4's NED velocity turned by the heading into level
body FLU (forward, left, up), for /estimate/velocity.

The clock is passed in as MONOTONIC seconds, as everywhere in flight_interface.
"""

import math

# PX4 publishes vehicle_status at 2 Hz (sooner on a change), the local position
# much faster, the land detector at least once a second (as the bridge).
STATUS_TIMEOUT = 1.5
POSITION_TIMEOUT = 0.5
LAND_TIMEOUT = 3.0
# The yaw setpoint never leads the real heading by more than this.
YAW_LEAD_CAP = math.radians(30.0)
# A longer step means a missed tick: the yaw setpoint is not moved then.
MAX_DT = 0.1


def _finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def wrap_angle(angle):
    """To (-pi, pi]. Example: 190 deg -> -170 deg."""
    wrapped = math.atan2(math.sin(angle), math.cos(angle))
    if wrapped == -math.pi:
        return math.pi
    return wrapped


def flu_to_frd(roll_deg, pitch_deg, yaw_rate_dps):
    """Our attitude (FLU, degrees) -> PX4's (FRD, radians): roll, pitch and
    yaw rate. Roll stays, pitch and yaw rate change sign (module docstring)."""
    roll = math.radians(roll_deg)
    pitch = -math.radians(pitch_deg)
    yaw_rate = -math.radians(yaw_rate_dps)
    return roll, pitch, yaw_rate


def quaternion_from_euler(roll, pitch, yaw):
    """Roll, pitch, yaw (rad, FRD body to NED, Z-Y-X order) -> (w, x, y, z),
    PX4's q_d order.

    Example: level, yaw 90 deg (facing east) -> (cos 45, 0, 0, sin 45) =
    (0.707, 0, 0, 0.707)."""
    half_roll = roll / 2.0
    half_pitch = pitch / 2.0
    half_yaw = yaw / 2.0
    cr = math.cos(half_roll)
    sr = math.sin(half_roll)
    cp = math.cos(half_pitch)
    sp = math.sin(half_pitch)
    cy = math.cos(half_yaw)
    sy = math.sin(half_yaw)
    w = cr * cp * cy + sr * sp * sy
    x = sr * cp * cy - cr * sp * sy
    y = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    return w, x, y, z


def ned_to_flu(v_north, v_east, v_down, heading):
    """PX4's NED velocity -> level body FLU (forward, left, up), turned by the
    heading (yaw from north, clockwise).

    Forward is (cos h, sin h) in (north, east) and right is (-sin h, cos h),
    so left is (sin h, -cos h). Example: heading 90 deg (east), moving east
    at 1 m/s -> forward 1, left 0."""
    c = math.cos(heading)
    s = math.sin(heading)
    forward = v_north * c + v_east * s
    left = v_north * s - v_east * c
    up = -v_down
    return forward, left, up


class Px4Logic:
    def __init__(
        self,
        mask,
        status_timeout=STATUS_TIMEOUT,
        position_timeout=POSITION_TIMEOUT,
        land_timeout=LAND_TIMEOUT,
    ):
        self.mask = mask
        self.status_timeout = status_timeout
        self.position_timeout = position_timeout
        self.land_timeout = land_timeout
        self.armed = False
        self.offboard = False
        self.landed = True
        self._status_t = None
        self._position_t = None
        self._landed_t = None
        self._heading = None
        self._heading_t = None
        self._yaw_sp = None
        self._last_command_t = None

    # ---------- inputs ----------
    def on_status(self, armed, offboard, now):
        self.armed = armed is True
        self.offboard = offboard is True
        self._status_t = now

    def on_position(self, heading, heading_good, now):
        """A local position message. A bad or non-finite heading is not used,
        but the message still counts for the link."""
        self._position_t = now
        if heading_good is True and _finite(heading):
            self._heading = float(heading)
            self._heading_t = now

    def on_landed(self, landed, now):
        self.landed = landed is True
        self._landed_t = now

    # ---------- checks ----------
    @staticmethod
    def _fresh(t, now, limit):
        return t is not None and 0.0 <= now - t <= limit

    def px4_fresh(self, now):
        status_ok = self._fresh(self._status_t, now, self.status_timeout)
        position_ok = self._fresh(self._position_t, now, self.position_timeout)
        return status_ok and position_ok

    def heading_fresh(self, now):
        return self._fresh(self._heading_t, now, self.position_timeout)

    # ---------- outputs ----------
    def drone_state(self, now):
        """The drone state for the guard (F13), or None while PX4 is not fresh
        (then the guard's watchdog sees the link lost)."""
        if not self.px4_fresh(now):
            return None
        airborne = (
            self._fresh(self._landed_t, now, self.land_timeout) and not self.landed
        )
        return {
            "esp_state": "STREAMING",
            "override_active": self.armed and self.offboard,
            "mask": self.mask,
            "airborne": airborne,
            "fc_link_ok": True,
            "armed": self.armed,
            "rx_time": now,
        }

    def reset_yaw(self):
        """Neutral: the next command starts from the real heading."""
        self._yaw_sp = None
        self._last_command_t = None

    def attitude(self, setpoint, now):
        """The guard's commanding Setpoint -> (q_d, thrust_body,
        yaw_sp_move_rate) for PX4, or None without a fresh heading (a stale
        one would hold or turn to the wrong yaw): then the backend sends
        neutral."""
        if not self.heading_fresh(now):
            self.reset_yaw()
            return None
        roll, pitch, yaw_rate = flu_to_frd(
            setpoint.roll, setpoint.pitch, setpoint.yaw_rate
        )
        heading = self._heading
        if self._yaw_sp is None:
            self._yaw_sp = heading
        else:
            dt = now - self._last_command_t
            if 0.0 < dt <= MAX_DT:
                self._yaw_sp = self._yaw_sp + yaw_rate * dt
        # Never further than YAW_LEAD_CAP from where the drone points.
        # Example: heading 10 deg, setpoint 50 deg -> lead 40 > 30 -> 40 deg.
        lead = wrap_angle(self._yaw_sp - heading)
        if lead > YAW_LEAD_CAP:
            lead = YAW_LEAD_CAP
        if lead < -YAW_LEAD_CAP:
            lead = -YAW_LEAD_CAP
        self._yaw_sp = wrap_angle(heading + lead)
        self._last_command_t = now
        q_d = quaternion_from_euler(roll, pitch, self._yaw_sp)
        thrust_body = (0.0, 0.0, -float(setpoint.throttle))
        return q_d, thrust_body, yaw_rate
