"""Cascade controller logic (#199), with NO ROS imports: a velocity target and
the measured velocity in, an attitude setpoint out. The same code runs in the
simulation and on the drone; only where the measured velocity comes from
changes (ADR-025 F1).

Inputs (body frame FLU, REP-103: x forward, y left, z up):
  * the velocity target from mission_node: vx, vy, vz (m/s), wz (rad/s,
    + = turn left)
  * the measured velocity: vx, vy, vz (m/s), from /estimate/velocity
Output, the /control/attitude_setpoint fields (ADR-025 C1.1):
  * roll, pitch (deg), yaw_rate (deg/s), throttle (0-1), active
  * reason (F17): why active is false (REASONS), "ok" while active
  * signs as /fc/attitude (C1.1 note): roll + = right side down, pitch + =
    nose down, yaw_rate + = turn left

Rules (ADR-025 §2.4):
  * The target is clamped first (F9): horizontal speed <= 0.5 m/s as a vector
    (direction kept), vertical <= 0.5 m/s, yaw rate <= 45 deg/s; a non-finite
    value counts as 0.
  * A target older than target_timeout (0.25 s) becomes zero: hover (F11).
  * hover_throttle 0.0 means "not measured" (F16): always inactive.
  * Active only while the measured velocity is fresh (F10, F11): with none,
    or one older than velocity_timeout, the output is `active` false
    (neutral, F8) and the integrators are cleared. A zero target with a
    fresh measured velocity is active: "hold zero velocity" (C2).
  * Roll and pitch never exceed tilt_cap_deg (15 deg, F4). Throttle stays
    within hover_throttle +- throttle_band (0.15, F14).
  * Only the axes in the override mask are commanded (8 yaw, 11 + roll and
    pitch, 15 + throttle). The others are output as level / hover throttle and
    their integrators stay at zero (the px4 backend fills them, F2).
  * The integrators run only while the output is applied: the last
    /flight/state from flight_interface is fresh and says control is possible
    (ADR-025 note 2026-11-27, PR #253 review). Otherwise they are cleared and
    the output is P only, still `active` (F10), so nothing winds up while the
    pilot or PX4 flies and the first applied step starts from zero.

Loops, one per axis, PI on the velocity error (no D term: the derivative of a
velocity is an acceleration, which is noisy):
  * Horizontal: error (m/s) -> wanted acceleration (m/s^2) -> tilt.
  * Vertical: error (m/s) -> throttle correction around the hover throttle.
  * Yaw: the clamped target yaw rate is passed through (C1.3).
Each integrator stores its term (ki times the integral of the error), so a
live ki change keeps the output continuous and ki 0 adds nothing. It stops
while its output is at its limit and would be pushed further (anti-windup;
for the throttle, the final value after the tilt correction and the band),
and skips a tick whose time step is not in (0, MAX_DT].

The clock is passed in as monotonic seconds, so tests need no sleeping.
"""

import json
import math
from dataclasses import dataclass, field

GRAVITY = 9.81  # m/s^2

# F9: the ceilings the flight_interface guard had in velocity units.
CEILING_LINEAR_MPS = 0.5
CEILING_YAW_DPS = 45.0

# REQ-SAF-017: a stale target is zero within 0.25 s.
MAX_INPUT_TIMEOUT = 0.25
# F4: the ground cap on roll and pitch; the controller may use less.
MAX_TILT_CAP_DEG = 15.0
# F14: throttle band around the hover throttle; the controller may use less.
MAX_THROTTLE_BAND = 0.15
# hover_throttle 0.0: nobody has measured it for this drone (F16).
HOVER_NOT_MEASURED = 0.0

# The `reason` field (F17).
OK = "ok"
NO_VELOCITY = "no_velocity"
STALE_VELOCITY = "stale_velocity"
NO_HOVER_THROTTLE = "no_hover_throttle"
STOPPED = "stopped"  # the node's last setpoint on the way out
REASONS = (OK, NO_VELOCITY, STALE_VELOCITY, NO_HOVER_THROTTLE, STOPPED)

# A longer step means a missed tick or a paused clock: no integration then.
MAX_DT = 0.1
# The node's tick at 50 Hz. A target must still be fresh at the NEXT tick, as
# in the flight_interface guard, so the zero goes out within target_timeout
# whatever the tick phase (F11, REQ-SAF-017; ADR-025 F11 note 2026-11-26).
DEFAULT_TICK_PERIOD = 0.02

# /flight/state older than this means "not applied": integrators cleared.
# flight_interface may publish it at 5 Hz (one every 0.2 s), so 0.5 s is 2.5
# of its slowest periods (ADR-025 note 2026-11-27).
FLIGHT_STATE_TIMEOUT = 0.5

# Axes the ground owns per override mask, as in mission_node and the guard.
MASK_AXES = {
    8: ("yaw",),
    11: ("x", "y", "yaw"),
    15: ("x", "y", "z", "yaw"),
}


def _finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def clamp_target(vx, vy, vz, wz):
    """F9: non-finite -> 0, then the ceilings. The horizontal speed is scaled
    down to CEILING_LINEAR_MPS keeping its direction; vz and wz (rad/s) are
    clamped on their own.

    Example: (0.6, 0.8) m/s is 1.0 m/s horizontally, twice the ceiling, so
    both are halved to (0.3, 0.4): still the same direction, 0.5 m/s."""
    values = []
    for value in (vx, vy, vz, wz):
        if _finite(value):
            values.append(float(value))
        else:
            values.append(0.0)
    vx, vy, vz, wz = values

    speed = math.hypot(vx, vy)
    if speed > CEILING_LINEAR_MPS:
        scale = CEILING_LINEAR_MPS / speed
        vx = vx * scale
        vy = vy * scale
    vz = _clamp(vz, -CEILING_LINEAR_MPS, CEILING_LINEAR_MPS)
    yaw_ceiling = math.radians(CEILING_YAW_DPS)
    wz = _clamp(wz, -yaw_ceiling, yaw_ceiling)
    return vx, vy, vz, wz


@dataclass
class Gains:
    """Starting values, tuned in the SITL hold test (#199 Phase 8).
    Horizontal: m/s^2 of wanted acceleration per m/s of error (and per m of
    accumulated error for ki). Vertical: throttle (0-1) per m/s of error."""

    kp_xy: float = 1.0
    ki_xy: float = 0.2
    kp_z: float = 0.3
    ki_z: float = 0.05


@dataclass
class Params:
    hover_throttle: float
    mask: int
    tilt_cap_deg: float = MAX_TILT_CAP_DEG
    throttle_band: float = MAX_THROTTLE_BAND
    target_timeout: float = MAX_INPUT_TIMEOUT
    velocity_timeout: float = MAX_INPUT_TIMEOUT
    gains: Gains = field(default_factory=Gains)

    def validate(self):
        """ValueError on the first bad value, so the node fails at start."""
        mask = self.mask
        # A real int, as the guard checks it: 8.0 == 8 in Python, True == 1.
        is_int = isinstance(mask, int) and not isinstance(mask, bool)
        if not is_int or mask not in MASK_AXES:
            raise ValueError(f"mask must be 8, 11 or 15, got {mask!r}")
        hover = self.hover_throttle
        if not _finite(hover) or not 0.0 <= hover < 1.0:
            raise ValueError(
                f"hover_throttle must be 0 (not measured) or in (0, 1), got {hover!r}"
            )
        band = self.throttle_band
        if not _finite(band) or not 0.0 < band <= MAX_THROTTLE_BAND:
            raise ValueError(
                f"throttle_band must be in (0, {MAX_THROTTLE_BAND}], got {band!r}"
            )
        measured = hover != HOVER_NOT_MEASURED
        if measured and (hover - band < 0.0 or hover + band > 1.0):
            raise ValueError(
                f"hover_throttle +- throttle_band must stay in 0-1, got {hover} +- {band}"
            )
        cap = self.tilt_cap_deg
        if not _finite(cap) or not 0.0 < cap <= MAX_TILT_CAP_DEG:
            raise ValueError(
                f"tilt_cap_deg must be in (0, {MAX_TILT_CAP_DEG}], got {cap!r}"
            )
        for name in ("target_timeout", "velocity_timeout"):
            timeout = getattr(self, name)
            if not _finite(timeout) or not 0.0 < timeout <= MAX_INPUT_TIMEOUT:
                raise ValueError(
                    f"{name} must be in (0, {MAX_INPUT_TIMEOUT}], got {timeout!r}"
                )
        for name in ("kp_xy", "ki_xy", "kp_z", "ki_z"):
            gain = getattr(self.gains, name)
            if not _finite(gain) or gain < 0.0:
                raise ValueError(f"{name} must be a number >= 0, got {gain!r}")


class _PiAxis:
    """PI on one axis with anti-windup. `term` is the integral part of the
    output itself (ki times the integral of the error), not the bare
    integral: a live ki change then keeps the output where it was, and with
    ki 0 the term does not grow. Example: error 0.1 m/s for 60 s at ki 0,
    then ki 0.2: the term is still 0, so no jump (with a bare integral it
    would be 0.2 * 6.0 = 1.2 m/s^2, about 7 deg of tilt)."""

    def __init__(self):
        self.term = 0.0

    def reset(self):
        self.term = 0.0

    def output(self, error, kp):
        """kp * error + term, not clamped."""
        return kp * error + self.term

    def integrate(self, error, ki, dt, at_high, at_low):
        """Add ki * error * dt to the term, unless dt is not usable or the
        output is already at its high (low) limit and the error pushes it
        higher (lower): anti-windup."""
        dt_ok = dt is not None and 0.0 < dt <= MAX_DT
        pushing_up = at_high and error > 0.0
        pushing_down = at_low and error < 0.0
        if dt_ok and not pushing_up and not pushing_down:
            self.term = self.term + ki * error * dt

    def step(self, error, kp, ki, limit, dt):
        """Integrate (anti-windup against +-limit), then the output clamped
        to +-limit."""
        before = self.output(error, kp)
        self.integrate(error, ki, dt, before >= limit, before <= -limit)
        return _clamp(self.output(error, kp), -limit, limit)


def tilted_throttle(correction, hover, band, lift_share):
    """The throttle for a vertical correction while tilted, and whether it
    is at the top or bottom of the band (for the z anti-windup).

    The correction is kept within +-band, then divided by the share of thrust
    that lifts, then clamped to hover +- band again (F14). Example: hover 0.70,
    band 0.15 (top 0.85), correction +0.12 -> 0.82 level. At 10 deg of pitch:
    0.82 / cos(10 deg) = 0.82 / 0.985 = 0.833, under the top. At 20 deg:
    0.82 / 0.940 = 0.873, clamped to 0.85 and at the top."""
    level_throttle = hover + _clamp(correction, -band, band)
    raw = level_throttle / lift_share
    high = hover + band
    low = hover - band
    return _clamp(raw, low, high), raw >= high, raw <= low


def applied_from_state(state):
    """True if a /flight/state dict says the output reaches the vehicle:
    link_ok (link fresh, flight_interface ACTIVE, no E-stop, FC link alive),
    STREAMING, override on and a known mask, the same gate as the guard's
    control_possible(). In the simulation override_active is "armed and in
    Offboard"."""
    if not isinstance(state, dict):
        return False
    mask = state.get("mask")
    known_mask = (
        isinstance(mask, int) and not isinstance(mask, bool) and mask in MASK_AXES
    )
    link_ok = state.get("link_ok") is True
    streaming = state.get("esp_state") == "STREAMING"
    override = state.get("override_active") is True
    return link_ok and streaming and override and known_mask


class Controller:
    """Keeps the latest target and measured velocity; step() gives the
    setpoint to publish now."""

    def __init__(self, params, tick_period=DEFAULT_TICK_PERIOD):
        params.validate()
        if not _finite(tick_period) or not 0.0 < tick_period <= MAX_DT:
            raise ValueError(f"tick_period must be in (0, {MAX_DT}], got {tick_period}")
        self.params = params
        self.tick_period = float(tick_period)
        # F15: hover_throttle equals flight_interface's. Live, it may only go
        # to 0.0 (not measured: always inactive, a pause) and back to this.
        self._start_hover = params.hover_throttle
        self.owned = MASK_AXES[params.mask]
        self._target = (0.0, 0.0, 0.0, 0.0)
        self._target_t = None
        self._velocity = (0.0, 0.0, 0.0)
        self._velocity_t = None
        self._applied = False
        self._state_t = None
        self._last_step_t = None
        self._x = _PiAxis()
        self._y = _PiAxis()
        self._z = _PiAxis()

    def retune(self, params):
        """New tuning values while running (gains, caps, band, timeouts).
        ValueError, and nothing changes, if a value is bad (the same checks
        as at start-up) or if the mask or the hover throttle would change:
        those must equal flight_interface's (F15), so they change only with
        a restart of both. The one exception: hover_throttle may go to 0.0
        (not measured, F16: always inactive, so flight_interface sends neutral)
        and back to its start value, which pauses the controller (ADR-025 F28
        note, 2026-11-26; make sim-hold's neutral phase uses it). The
        integrators keep their terms (ki already inside), so a gain change
        moves the output only by the kp part: no jump from a changed ki."""
        params.validate()
        if params.mask != self.params.mask:
            raise ValueError("mask cannot change while running (restart the node)")
        allowed = (HOVER_NOT_MEASURED, self._start_hover)
        if params.hover_throttle not in allowed:
            raise ValueError(
                "hover_throttle can only change to 0.0 (pause) or back to "
                f"{self._start_hover} while running (restart the node)"
            )
        self.params = params

    # ---------- inputs ----------
    def on_target(self, vx, vy, vz, wz, now):
        """A velocity target from mission_node, body FLU, wz in rad/s."""
        self._target = clamp_target(vx, vy, vz, wz)
        self._target_t = now

    def on_velocity(self, vx, vy, vz, now):
        """A measured velocity, body FLU. A non-finite value is dropped, so
        the last good one goes stale and the output becomes inactive."""
        for value in (vx, vy, vz):
            if not _finite(value):
                return
        self._velocity = (float(vx), float(vy), float(vz))
        self._velocity_t = now

    def on_flight_state(self, data, now):
        """The JSON text of /flight/state. Returns False (and counts as not
        applied) if it is not a JSON object."""
        try:
            state = json.loads(data)
        except (TypeError, ValueError):
            state = None
        self._state_t = now
        self._applied = applied_from_state(state)
        return isinstance(state, dict)

    # ---------- freshness ----------
    @staticmethod
    def _fresh(t, now, limit):
        return t is not None and 0.0 <= now - t <= limit

    def target_fresh(self, now):
        """Fresh only if still fresh at the next tick (F11)."""
        next_tick = now + self.tick_period
        return self._fresh(self._target_t, next_tick, self.params.target_timeout)

    def velocity_fresh(self, now):
        return self._fresh(self._velocity_t, now, self.params.velocity_timeout)

    def output_applied(self, now):
        """The last /flight/state is fresh and says control is possible."""
        fresh = self._fresh(self._state_t, now, FLIGHT_STATE_TIMEOUT)
        return fresh and self._applied

    # ---------- output ----------
    def neutral(self, reason):
        """F8: no command, with the reason (F17). The numbers are level and
        the hover throttle, so that nothing reading them by mistake would tilt
        the drone; only `active` false counts."""
        return {
            "roll": 0.0,
            "pitch": 0.0,
            "yaw_rate": 0.0,
            "throttle": self.params.hover_throttle,
            "active": False,
            "reason": reason,
        }

    def inactive_reason(self, now):
        """Why the output is inactive now, or None if it can be active."""
        if self.params.hover_throttle == HOVER_NOT_MEASURED:
            return NO_HOVER_THROTTLE
        if self._velocity_t is None:
            return NO_VELOCITY
        if not self.velocity_fresh(now):
            return STALE_VELOCITY
        return None

    def _reset(self):
        self._x.reset()
        self._y.reset()
        self._z.reset()

    def step(self, now):
        """The setpoint for this tick, as the /control/attitude_setpoint dict."""
        if self._last_step_t is None:
            dt = None
        else:
            dt = now - self._last_step_t
        self._last_step_t = now

        reason = self.inactive_reason(now)
        if reason is not None:
            self._reset()
            return self.neutral(reason)

        # Output not applied (override off, not in Offboard, flight_interface
        # not active or in E-stop): no integration, integrators cleared.
        if not self.output_applied(now):
            self._reset()
            dt = None

        if self.target_fresh(now):
            target_x, target_y, target_z, target_wz = self._target
        else:
            target_x, target_y, target_z, target_wz = (0.0, 0.0, 0.0, 0.0)
        measured_x, measured_y, measured_z = self._velocity
        params = self.params
        gains = params.gains

        # Horizontal. The tilt cap limits the acceleration the loop may ask
        # for: a = g * tan(cap). Example: 15 deg -> 9.81 * 0.268 = 2.63 m/s^2.
        tilt_cap = math.radians(params.tilt_cap_deg)
        accel_limit = GRAVITY * math.tan(tilt_cap)
        roll = 0.0
        pitch = 0.0
        if "x" in self.owned:
            error_x = target_x - measured_x
            accel_x = self._x.step(error_x, gains.kp_xy, gains.ki_xy, accel_limit, dt)
            # Forward acceleration needs the nose down, which is pitch + in
            # REP-103. tilt = atan(a / g). Example: 1 m/s^2 forward ->
            # atan(1 / 9.81) = 5.8 deg of pitch.
            pitch = math.atan(accel_x / GRAVITY)
        else:
            self._x.reset()
        if "y" in self.owned:
            error_y = target_y - measured_y
            accel_y = self._y.step(error_y, gains.kp_xy, gains.ki_xy, accel_limit, dt)
            # Acceleration to the LEFT (y +) needs the left side down, which is
            # roll - in REP-103 (roll + = right side down). Example: 1 m/s^2
            # left -> roll = -5.8 deg.
            roll = -math.atan(accel_y / GRAVITY)
        else:
            self._y.reset()

        # Vertical: hover throttle plus a correction within the band. Tilted,
        # only cos(roll) * cos(pitch) of the thrust lifts, so the level value
        # is divided by it. Example: hover 0.70 at 10 deg of pitch ->
        # 0.70 / cos(10 deg) = 0.70 / 0.985 = 0.711. The result is clamped
        # to the band again (F14). Anti-windup looks at that final throttle.
        hover = params.hover_throttle
        band = params.throttle_band
        throttle = hover
        if "z" in self.owned:
            error_z = target_z - measured_z
            lift_share = math.cos(roll) * math.cos(pitch)
            before = self._z.output(error_z, gains.kp_z)
            _, at_high, at_low = tilted_throttle(before, hover, band, lift_share)
            self._z.integrate(error_z, gains.ki_z, dt, at_high, at_low)
            correction = self._z.output(error_z, gains.kp_z)
            throttle, _, _ = tilted_throttle(correction, hover, band, lift_share)
        else:
            self._z.reset()

        yaw_rate = 0.0
        if "yaw" in self.owned:
            yaw_rate = math.degrees(target_wz)

        return {
            "roll": math.degrees(roll),
            "pitch": math.degrees(pitch),
            "yaw_rate": yaw_rate,
            "throttle": throttle,
            "active": True,
            "reason": OK,
        }
