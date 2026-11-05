"""Logic of px4_offboard_bridge, with NO ROS imports (unit-testable anywhere).

SIMULATION ONLY (#86). Stand-in for the PX4 backend of flight_interface
(ADR-019); never use it with the real drone.

Frames:
  * mission_node publishes /mission/velocity_target in the BODY frame, FLU
    (REP-103): x forward, y LEFT, z UP, angular.z + = counter-clockwise.
  * PX4 offboard velocity setpoints are in the LOCAL frame, NED: x north,
    y east, z DOWN, yawspeed + = clockwise. PX4's `heading` is the yaw from
    north, clockwise, in radians.

Conversion (yaw = PX4 heading):
    v_north =  vx * cos(yaw) + vy * sin(yaw)
    v_east  =  vx * sin(yaw) - vy * cos(yaw)
    v_down  = -vz
    yawspeed = -wz
Derivation: body forward is (cos yaw, sin yaw) in (north, east), body right
is (-sin yaw, cos yaw), and FLU left = -right.
"""

import math

# Axes the ground owns per override mask, as in mission_node ("04 Mission Node" §5.3).
MASK_AXES = {
    8: {"yaw"},
    11: {"x", "y", "yaw"},
    15: {"x", "y", "z", "yaw"},
}

# Same hard ceilings as mission_node: the bridge never sends more, whatever arrives.
CEILING_LINEAR_MPS = 0.5
CEILING_YAW_RPS = math.radians(45.0)

ZERO = (0.0, 0.0, 0.0, 0.0)


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def flu_body_to_ned(vx, vy, vz, wz, yaw):
    """Body FLU velocity + yaw rate -> NED velocity + yawspeed (see the module doc)."""
    c, s = math.cos(yaw), math.sin(yaw)
    return (vx * c + vy * s, vx * s - vy * c, -vz, -wz)


def apply_mask(vx, vy, vz, wz, mask):
    """Zero every axis the ground does not own. An unknown mask owns nothing."""
    owned = MASK_AXES.get(mask, set())
    return (
        vx if "x" in owned else 0.0,
        vy if "y" in owned else 0.0,
        vz if "z" in owned else 0.0,
        wz if "yaw" in owned else 0.0,
    )


def clamp_target(vx, vy, vz, wz):
    """Non-finite -> 0, then each axis to the hard ceiling."""

    def lim(v, top):
        return max(-top, min(top, v)) if _finite(v) else 0.0

    return (
        lim(vx, CEILING_LINEAR_MPS),
        lim(vy, CEILING_LINEAR_MPS),
        lim(vz, CEILING_LINEAR_MPS),
        lim(wz, CEILING_YAW_RPS),
    )


# REQ-SAF-017: the zero must be out no later than this after the last target.
MAX_TARGET_TIMEOUT = 0.25
# PX4 publishes vehicle_status at 2 Hz (every 500 ms, sooner on a change) and
# the local position much faster, so each gets its own freshness limit.
STATUS_TIMEOUT = 1.5
POSITION_TIMEOUT = 0.5


class BridgeLogic:
    """Keeps the latest target and PX4 state, and decides what to send.

    The clock is passed in as MONOTONIC seconds (time.monotonic() in the node),
    so a wall-clock step cannot make an old target look fresh, and tests need
    no sleeping.

    Which input gates which output:
      * setpoint(): needs a fresh target AND a fresh heading; otherwise zero.
      * flight_state()["link_ok"]: status within STATUS_TIMEOUT and position
        messages within POSITION_TIMEOUT. A heading that is not yet good for
        control (the first seconds after PX4 starts) does not drop the link:
        it only holds the setpoint at zero.
    """

    def __init__(
        self,
        mask=15,
        target_timeout=MAX_TARGET_TIMEOUT,
        tick_period=0.05,
        status_timeout=STATUS_TIMEOUT,
        position_timeout=POSITION_TIMEOUT,
    ):
        if mask not in MASK_AXES:
            raise ValueError(f"mask must be 8, 11 or 15, got {mask!r}")
        self.mask = mask
        # Never longer than REQ-SAF-017 allows; NaN or <= 0 also gives the maximum.
        if not _finite(target_timeout) or not 0 < target_timeout <= MAX_TARGET_TIMEOUT:
            target_timeout = MAX_TARGET_TIMEOUT
        self.target_timeout = target_timeout
        self.tick_period = tick_period
        self.status_timeout = status_timeout
        self.position_timeout = position_timeout
        self._target = ZERO
        self._target_t = None
        self._yaw = None
        self._yaw_t = None
        self._position_t = None
        self.armed = False
        self.offboard = False
        self.landed = True  # until PX4 says otherwise
        self._status_t = None
        self.stale_targets = 0  # ticks that sent zero after a target had arrived

    # ---------- inputs ----------
    def on_target(self, vx, vy, vz, wz, now):
        self._target = (vx, vy, vz, wz)
        self._target_t = now

    def on_position(self, yaw, good, now):
        """A local position message. `good` is PX4's heading_good_for_control:
        a bad or NaN heading is not used, but the message still counts for the link."""
        self._position_t = now
        if good and _finite(yaw):
            self._yaw = yaw
            self._yaw_t = now

    def on_status(self, armed, offboard, now):
        self.armed = bool(armed)
        self.offboard = bool(offboard)
        self._status_t = now

    def on_landed(self, landed):
        self.landed = bool(landed)

    # ---------- checks ----------
    @staticmethod
    def _fresh(t, now, limit):
        return t is not None and 0 <= now - t <= limit

    def target_fresh(self, now):
        """Fresh only if it is still fresh at the NEXT tick too, like mission_node:
        so the zero goes out within target_timeout whatever the tick phase."""
        return self._fresh(self._target_t, now + self.tick_period, self.target_timeout)

    def heading_fresh(self, now):
        return self._fresh(self._yaw_t, now, self.position_timeout)

    def px4_fresh(self, now):
        return self._fresh(self._status_t, now, self.status_timeout) and self._fresh(
            self._position_t, now, self.position_timeout
        )

    # ---------- outputs ----------
    def setpoint(self, now):
        """NED (v_north, v_east, v_down, yawspeed) to send now.

        Zero (hover) when the target would be older than `target_timeout` by the
        next tick (REQ-SAF-017) or the heading is missing or stale (a frozen
        heading would send the drone the wrong way). Axes outside the mask are
        zero (REQ-SAF-018).
        """
        if not self.target_fresh(now) or not self.heading_fresh(now):
            if self._target_t is not None:
                self.stale_targets += 1
            return ZERO
        body = apply_mask(*clamp_target(*self._target), self.mask)
        return flu_body_to_ned(*body, self._yaw)

    def flight_state(self, now):
        """The /flight/state fields mission_node expects ("04 Mission Node" §3.2).

        link_ok: PX4 status and position are fresh. override_active: armed and in
        Offboard (the sim's "takeover switch on"). airborne: PX4 says not landed.
        esp_state is synthetic in the sim (there is no ESP): it only mirrors link_ok.
        """
        link = self.px4_fresh(now)
        return {
            "link_ok": link,
            "esp_state": "STREAMING" if link else "LINK_FAILSAFE",
            "override_active": link and self.armed and self.offboard,
            "mask": self.mask,
            "airborne": not self.landed,
        }
