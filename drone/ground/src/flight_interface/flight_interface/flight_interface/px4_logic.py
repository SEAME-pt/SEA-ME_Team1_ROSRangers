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

Neutral (F19): PX4 holds position itself. NeutralHold gives the point: the
drone's position and heading when neutral starts in Offboard (ADR-025 F19 note,
2026-11-25). A zero velocity alone drifted up in SITL: PX4's own vz is biased
(the F1 note), so the drone climbed while the controller was stopped.

Drone state for the guard (F13):
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

Simulated pilot (SimPilot, ADR-025 F2 as changed 2026-11-24): on the axes the
ground does not own (masks 8 and 11), the safety pilot's sticks fly the real
drone. The simulation has no pilot, and a fixed hover throttle there is open
loop: the drone climbed 72 m in 60 s under mask 11. SimPilot stands in for
the pilot: it holds zero velocity on those axes, using the same measured
velocity. The ground's setpoint on its own axes is never changed.

The clock is passed in as MONOTONIC seconds, as everywhere in flight_interface.
"""

import dataclasses
import math

from flight_interface.guard import MASK_AXES, THROTTLE_BAND, TILT_CAP_DEG

# PX4 publishes vehicle_status at 2 Hz (sooner on a change), the local position
# much faster, the land detector at least once a second.
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


# ---------- the neutral hold point (SIMULATION ONLY) ----------
# A position or heading older than this is not used for the hold point (the
# same limit as flight_interface's setpoints).
HOLD_POSITION_TIMEOUT = 0.25


class NeutralHold:
    """The point PX4 holds while the backend sends neutral (F19).

    Rules (ADR-025 F19 note, 2026-11-25):
      1. Not in Offboard (on the ground, PX4's own take-off): the point follows
         the drone, so the switch to Offboard never sends it back to where
         neutral started.
      2. In Offboard, the first neutral tick freezes the drone's position
         (north, east, down, NED metres) and heading (rad).
      3. A commanding setpoint (the controller is back) or the E-stop releases
         it: the next gap freezes a new point.
      4. An axis without a fresh, valid value is NaN: PX4 holds zero velocity
         there, as before. Example: z valid and x/y not -> a height hold only.
    """

    def __init__(self, timeout=HOLD_POSITION_TIMEOUT):
        self.timeout = timeout
        self._xy = None  # (north, east)
        self._xy_t = None
        self._z = None  # down
        self._z_t = None
        self._heading = None
        self._heading_t = None
        self._point = None  # frozen (north, east, down, heading), NaN = none

    def on_position(
        self, north, east, xy_valid, down, z_valid, heading, heading_good, now
    ):
        """A local position message. Invalid or non-finite values are not used."""
        if xy_valid is True and _finite(north) and _finite(east):
            self._xy = (float(north), float(east))
            self._xy_t = now
        if z_valid is True and _finite(down):
            self._z = float(down)
            self._z_t = now
        if heading_good is True and _finite(heading):
            self._heading = float(heading)
            self._heading_t = now

    def _fresh(self, t, now):
        return t is not None and 0.0 <= now - t <= self.timeout

    def _current(self, now):
        """(north, east, down, heading) now, NaN for each stale axis."""
        north = math.nan
        east = math.nan
        down = math.nan
        heading = math.nan
        if self._fresh(self._xy_t, now):
            north, east = self._xy
        if self._fresh(self._z_t, now):
            down = self._z
        if self._fresh(self._heading_t, now):
            heading = self._heading
        return north, east, down, heading

    def release(self):
        """The controller took over (or E-stop): the next gap freezes anew."""
        self._point = None

    def target(self, offboard, now):
        """The point for this neutral tick: (north, east, down, heading)."""
        if offboard is not True:
            self._point = None
            return self._current(now)
        if self._point is None:
            self._point = self._current(now)
        return self._point


# ---------- the simulated pilot (SIMULATION ONLY) ----------
GRAVITY = 9.81  # m/s^2
# The pilot's PI gains: the controller's starting gains (controller_logic.py,
# Gains), which hold the x500 within REQ-NAV-001 in SITL. Its own copy: the
# pilot stands in for a person, not for the ground's controller.
PILOT_KP_XY = 1.0
PILOT_KI_XY = 0.2
PILOT_KP_Z = 0.3
PILOT_KI_Z = 0.05
# A measured velocity older than this: the pilot cannot fly, the backend
# sends neutral (PX4's own hold) instead.
PILOT_VELOCITY_TIMEOUT = 0.25


def _clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


class _PilotAxis:
    """PI on one axis with an output limit and anti-windup (as the
    controller's): no integration while the output is at its limit and the
    error pushes it further, or when the time step is not in (0, MAX_DT]."""

    def __init__(self, kp, ki):
        self.kp = kp
        self.ki = ki
        self.integral = 0.0

    def reset(self):
        self.integral = 0.0

    def step(self, error, limit, dt):
        output = self.kp * error + self.ki * self.integral
        dt_ok = dt is not None and 0.0 < dt <= MAX_DT
        pushing_up = output >= limit and error > 0.0
        pushing_down = output <= -limit and error < 0.0
        if dt_ok and not pushing_up and not pushing_down:
            self.integral = self.integral + error * dt
            output = self.kp * error + self.ki * self.integral
        return _clamp(output, -limit, limit)


class SimPilot:
    """Flies the axes the ground does not own, holding zero velocity on each.

    Mask 15: nothing to fly, the setpoint goes out unchanged. Mask 11: the
    throttle. Mask 8: the throttle and roll / pitch. Yaw is always the
    ground's."""

    def __init__(self, mask, hover_throttle):
        self.owned = MASK_AXES[mask]
        self.hover = hover_throttle
        self._x = _PilotAxis(PILOT_KP_XY, PILOT_KI_XY)
        self._y = _PilotAxis(PILOT_KP_XY, PILOT_KI_XY)
        self._z = _PilotAxis(PILOT_KP_Z, PILOT_KI_Z)
        self._velocity = None  # (forward, left, up) m/s, level body FLU
        self._velocity_t = None
        self._last_t = None

    def needed(self):
        """False under mask 15: the ground owns every axis."""
        return len(self.owned) < len(MASK_AXES[15])

    def on_velocity(self, forward, left, up, now):
        self._velocity = (forward, left, up)
        self._velocity_t = now

    def reset(self):
        """Neutral: the next commanding setpoint starts from clean integrators."""
        self._x.reset()
        self._y.reset()
        self._z.reset()
        self._last_t = None

    def fly(self, setpoint, now):
        """The setpoint with the pilot's values on the axes the ground does
        not own, or None without a fresh measured velocity (then the backend
        sends neutral). Under mask 15 the setpoint unchanged."""
        if not self.needed():
            return setpoint
        fresh = (
            self._velocity_t is not None
            and 0.0 <= now - self._velocity_t <= PILOT_VELOCITY_TIMEOUT
        )
        if not fresh:
            self.reset()
            return None
        if self._last_t is None:
            dt = None
        else:
            dt = now - self._last_t
        self._last_t = now
        forward, left, up = self._velocity

        roll = setpoint.roll
        pitch = setpoint.pitch
        # Horizontal (mask 8): wanted acceleration -> tilt, as the controller.
        # Example: drifting forward at 0.1 m/s -> accel -0.1 m/s^2 ->
        # pitch = atan(-0.1 / 9.81) = -0.58 deg (nose up, REP-103).
        accel_limit = GRAVITY * math.tan(math.radians(TILT_CAP_DEG))
        if "x" not in self.owned:
            accel_x = self._x.step(0.0 - forward, accel_limit, dt)
            pitch = math.degrees(math.atan(accel_x / GRAVITY))
        if "y" not in self.owned:
            accel_y = self._y.step(0.0 - left, accel_limit, dt)
            roll = -math.degrees(math.atan(accel_y / GRAVITY))

        throttle = setpoint.throttle
        if "z" not in self.owned:
            # Hover plus a correction within the band, divided by the share
            # of thrust that lifts while tilted. Example: climbing at 0.2 m/s
            # -> correction 0.3 * -0.2 = -0.06 -> 0.73 - 0.06 = 0.67 level,
            # 0.67 / cos(5 deg) = 0.673 at 5 deg of roll.
            correction = self._z.step(0.0 - up, THROTTLE_BAND, dt)
            level_throttle = self.hover + correction
            lift_share = math.cos(math.radians(roll)) * math.cos(math.radians(pitch))
            throttle = _clamp(
                level_throttle / lift_share,
                self.hover - THROTTLE_BAND,
                self.hover + THROTTLE_BAND,
            )
        return dataclasses.replace(setpoint, roll=roll, pitch=pitch, throttle=throttle)
