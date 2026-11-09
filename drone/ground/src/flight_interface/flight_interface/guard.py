"""Safety logic of the flight_interface lifecycle node (#103), with NO ROS imports.

It decides, on every tick, whether a setpoint goes to the drone and which one,
and what /flight/state tells mission_node. The node (link_node.py) only wires
it to ROS and to a backend.

Rules:
  * Setpoints go out only while ACTIVE (lifecycle) and not in E-stop.
  * E-stop cuts them at once (nothing more is sent, so the ESP enters its own
    250 ms link failsafe, ADR-004). It is latched: only an explicit reset
    clears it, and after the reset a NEW target is needed (an old one is never
    replayed). Same after activate.
  * Watchdog: the drone link is lost when no valid state from the drone arrived
    within link_timeout. Then the setpoint is zero and link_ok is false, so
    mission_node goes IDLE (REQ-SAF-012).
  * A target older than target_timeout by the next tick gives zero
    (REQ-SAF-017), the same rule as mission_node and the PX4 bridge.
  * A non-zero setpoint needs the drone itself to say control is possible:
    esp_state STREAMING, override_active true and a known mask, the same gate
    as mission_node. Axes outside the mask are zero (REQ-SAF-018), as in the
    PX4 bridge.
  * Same hard ceilings as mission_node, applied again here: the horizontal
    speed as a whole (so a diagonal is not faster), the vertical speed and the
    yaw rate. Non-finite values become 0.
  * There is no way to arm here: this class has no arm request at all
    (REQ-SAF-023). Arming is always an operator action.

The clock is passed in as MONOTONIC seconds (time.monotonic() in the node), so
tests need no sleeping and a wall-clock step cannot make old data look fresh.
"""

import math

ZERO = (0.0, 0.0, 0.0, 0.0)

CEILING_LINEAR_MPS = 0.5
CEILING_YAW_RPS = math.radians(45.0)

# REQ-SAF-017: the zero must be out no later than this after the last target.
MAX_TARGET_TIMEOUT = 0.25
# The ESP sends STATE at 25 Hz (Protocol Spec v0 §3); mission_node calls a
# /flight/state older than 0.5 s lost, so the watchdog uses the same 0.5 s.
LINK_TIMEOUT = 0.5

# esp_state values the drone may report (Protocol Spec v0 §3.1, STATE).
ESP_STATES = ("BOOT", "NO_SESSION", "SESSION_IDLE", "STREAMING", "LINK_FAILSAFE")

# Axes the ground owns per override mask, as in mission_node ("04 Mission Node" §5.3).
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


def apply_mask(vx, vy, vz, wz, mask):
    """Zero every axis the ground does not own. An unknown mask owns nothing."""
    owned = MASK_AXES.get(mask, ())
    return (
        vx if "x" in owned else 0.0,
        vy if "y" in owned else 0.0,
        vz if "z" in owned else 0.0,
        wz if "yaw" in owned else 0.0,
    )


def clamp_target(vx, vy, vz, wz):
    """Non-finite -> 0, then the hard ceilings: the horizontal speed (x, y) is
    scaled down to CEILING_LINEAR_MPS keeping its direction; z and the yaw rate
    are clamped on their own."""

    def lim(v, top):
        return max(-top, min(top, v)) if _finite(v) else 0.0

    vx, vy = (float(v) if _finite(v) else 0.0 for v in (vx, vy))
    speed = math.hypot(vx, vy)
    if speed > CEILING_LINEAR_MPS:
        scale = CEILING_LINEAR_MPS / speed
        vx, vy = vx * scale, vy * scale
    return (vx, vy, float(lim(vz, CEILING_LINEAR_MPS)), float(lim(wz, CEILING_YAW_RPS)))


class Guard:
    """Keeps the latest target and drone state, and decides what to send."""

    def __init__(
        self,
        target_timeout=MAX_TARGET_TIMEOUT,
        link_timeout=LINK_TIMEOUT,
        tick_period=0.02,
    ):
        # Never longer than REQ-SAF-017 allows; NaN or <= 0 also gives the maximum.
        if not _finite(target_timeout) or not 0 < target_timeout <= MAX_TARGET_TIMEOUT:
            target_timeout = MAX_TARGET_TIMEOUT
        if not _finite(link_timeout) or link_timeout <= 0:
            link_timeout = LINK_TIMEOUT
        if not _finite(tick_period) or tick_period <= 0:
            raise ValueError(f"tick_period must be > 0, got {tick_period!r}")
        self.target_timeout = target_timeout
        self.link_timeout = link_timeout
        self.tick_period = tick_period

        self.active = False
        self.estop = False
        self.estop_reason = ""
        self._target = ZERO
        self._target_t = None
        self._drone = None  # last valid state from the drone (dict)
        self._drone_t = None

    # ---------- lifecycle and E-stop ----------
    def activate(self):
        """Start sending. The target is dropped: a fresh one is needed first."""
        self.active = True
        self._drop_target()

    def deactivate(self):
        self.active = False
        self._drop_target()

    def trigger_estop(self, reason):
        """Latched until reset_estop(). Nothing is sent while it holds."""
        self.estop = True
        self.estop_reason = str(reason) or "estop"
        self._drop_target()

    def reset_estop(self):
        """The explicit reset. Returns False if there was no E-stop. The target
        is dropped again, so only a target that arrives after the reset is used."""
        if not self.estop:
            return False
        self.estop = False
        self.estop_reason = ""
        self._drop_target()
        return True

    def _drop_target(self):
        self._target = ZERO
        self._target_t = None

    # ---------- inputs ----------
    def on_target(self, vx, vy, vz, wz, now):
        """A velocity target from mission_node (body FLU). Ignored while inactive
        or in E-stop, so nothing queued then can come out later."""
        if not self.active or self.estop:
            return
        self._target = (vx, vy, vz, wz)
        self._target_t = now

    def on_drone_state(self, state, now):
        """A valid state from the drone (the backend checked the frame). `state`
        has esp_state, override_active, mask, airborne; anything else is kept
        but not used. A state with an unknown esp_state is not a valid state."""
        if not isinstance(state, dict) or state.get("esp_state") not in ESP_STATES:
            return
        self._drone = dict(state)
        self._drone_t = now

    # ---------- checks ----------
    @staticmethod
    def _fresh(t, now, limit):
        return t is not None and 0 <= now - t <= limit

    def target_fresh(self, now):
        """Fresh only if still fresh at the NEXT tick, like mission_node, so the
        zero goes out within target_timeout whatever the tick phase."""
        return self._fresh(self._target_t, now + self.tick_period, self.target_timeout)

    def link_fresh(self, now):
        return self._fresh(self._drone_t, now, self.link_timeout)

    # ---------- outputs ----------
    def command(self, now):
        """What to send now: None (send nothing) or (vx, vy, vz, wz) body FLU.

        None while inactive or in E-stop. Zero when the target is missing or
        stale, the drone link is lost, or the drone does not say control is
        possible (STREAMING, override on, a known mask). Otherwise the target,
        masked to the owned axes and then clamped.
        """
        if not self.active or self.estop:
            return None
        if not self.target_fresh(now) or not self.control_possible(now):
            return ZERO
        mask = self._drone["mask"]
        return clamp_target(*apply_mask(*self._target, mask))

    def control_possible(self, now):
        """The drone link is fresh and the drone reports STREAMING, override on
        and a known mask: the same gate mission_node applies (§3.2)."""
        if not self.link_fresh(now):
            return False
        drone = self._drone
        return (
            drone.get("esp_state") == "STREAMING"
            and drone.get("override_active") is True
            and drone.get("mask") in MASK_AXES
        )

    def flight_state(self, now):
        """The /flight/state fields mission_node expects ("04 Mission Node" §3.2),
        plus active / estop / estop_reason for display.

        link_ok: the ground can control the drone now, i.e. the link is fresh,
        the node is ACTIVE and not in E-stop. If not, mission_node goes IDLE.
        The other fields come from the last valid drone state; with no fresh
        state they fall back to the safe values (no override, no mask).
        """
        fresh = self.link_fresh(now)
        drone = self._drone if fresh else {}
        return {
            "link_ok": fresh and self.active and not self.estop,
            "esp_state": drone.get("esp_state"),  # None: nothing fresh to report
            "override_active": drone.get("override_active") is True,
            "mask": drone.get("mask"),
            "airborne": drone.get("airborne") is True,
            "active": self.active,
            "estop": self.estop,
            "estop_reason": self.estop_reason,
        }
