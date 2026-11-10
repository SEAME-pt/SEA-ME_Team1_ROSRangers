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
    (the rule of mission_node's REQ-SAF-017, applied again here), as in the
    PX4 bridge.
  * A non-zero setpoint needs the drone itself to say control is possible:
    esp_state STREAMING, override_active true and a known mask, the same gate
    as mission_node, and the drone must not report the FC link lost
    (fc_link_ok false, EVENT 0x0201, #179). Axes outside the mask are zero (mission_node's REQ-SAF-018, applied again here), as in the
    PX4 bridge.
  * Same hard ceilings as mission_node, applied again here: the horizontal
    speed as a whole (so a diagonal is not faster), the vertical speed and the
    yaw rate. Non-finite values become 0.
  * The guard never asks for ARM (REQ-SAF-023). arm_allowed() only answers
    whether the operator's ~/arm_control may send it now (decision A4).
  * RESUME after a link failsafe is an operator action too (decision A3,
    2026-11-09): request_resume() only arms a pending request, and resume_due()
    says when the backend may send RESUME_REQUEST: after RESUME_WINDOW of
    neutral setpoints with no gap of RESUME_MAX_GAP or more, while the drone
    still reports LINK_FAILSAFE. The guard never starts a resume by itself.

The clock is passed in as MONOTONIC seconds (time.monotonic() in the node), so
tests need no sleeping and a wall-clock step cannot make old data look fresh.
"""

import math

ZERO = (0.0, 0.0, 0.0, 0.0)

CEILING_LINEAR_MPS = 0.5
CEILING_YAW_RPS = math.radians(45.0)

# The zero must be out no later than this after the last target (the rule of
# mission_node's REQ-SAF-017).
MAX_TARGET_TIMEOUT = 0.25
# The ESP sends STATE at 25 Hz (Protocol Spec v0 §3); mission_node calls a
# /flight/state older than 0.5 s lost, so the watchdog uses the same 0.5 s.
LINK_TIMEOUT = 0.5

# esp_state values the drone may report (Protocol Spec v0 §3.1, STATE).
ESP_STATES = ("BOOT", "NO_SESSION", "SESSION_IDLE", "STREAMING", "LINK_FAILSAFE")

# RESUME (Protocol Spec v0 §3.1 code 3): the ESP wants >= 500 ms of mode-0
# setpoints with no gap of T_fresh (150 ms) or more. The ground waits a little
# longer and allows a smaller gap, so jitter on the way does not make the ESP
# refuse it.
RESUME_WINDOW = 0.6
RESUME_MAX_GAP = 0.1
# A pending resume that never became due (no setpoints going out) is dropped.
RESUME_TIMEOUT = 2.0

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


def _is_mask(mask):
    """8, 11 or 15 as a real int, as mission_node checks it (8.0 or True is not)."""
    return isinstance(mask, int) and not isinstance(mask, bool) and mask in MASK_AXES


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
        # Never longer than the 0.25 s rule allows; NaN or <= 0 also gives the maximum.
        if not _finite(target_timeout) or not 0 < target_timeout <= MAX_TARGET_TIMEOUT:
            target_timeout = MAX_TARGET_TIMEOUT
        # Never longer than LINK_TIMEOUT either: a longer one would let a silent
        # drone look alive and switch the watchdog off (REQ-SAF-012).
        if not _finite(link_timeout) or not 0 < link_timeout <= LINK_TIMEOUT:
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

        self.resume_pending = False
        self._resume_t = None  # when the operator asked
        self._last_sent_t = None  # last setpoint handed to the backend
        self._neutral_since = None  # start of the current run of neutral sends

    # ---------- lifecycle and E-stop ----------
    def activate(self):
        """Start sending. The target is dropped: a fresh one is needed first."""
        self.active = True
        self._drop_target()
        self.cancel_resume()

    def deactivate(self):
        self.active = False
        self._drop_target()
        self.cancel_resume()

    def trigger_estop(self, reason):
        """Latched until reset_estop(). Nothing is sent while it holds."""
        self.estop = True
        self.estop_reason = str(reason) or "estop"
        self._drop_target()
        self.cancel_resume()

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
        has esp_state, override_active, mask, airborne (and failsafe_output from
        the ESP backend); anything else is kept but not used. A state with an
        unknown esp_state is not a valid state. `now` is when the state was
        received; a state older than the one already held is ignored, so a late
        frame can never make the link look fresher than it is."""
        if not isinstance(state, dict) or state.get("esp_state") not in ESP_STATES:
            return
        if not _finite(now) or (self._drone_t is not None and now < self._drone_t):
            return
        self._drone = dict(state)
        self._drone_t = now

    # ---------- RESUME (operator only, decision A3) ----------
    def request_resume(self, now):
        """The operator asks to resume after a link failsafe. Returns (ok, why).

        Refused unless the node is active, not in E-stop, the link is fresh and
        the drone reports LINK_FAILSAFE. When accepted, the request is only
        pending: resume_due() says when RESUME_REQUEST may go out."""
        if not self.active:
            return False, "not active"
        if self.estop:
            return False, "E-stop latched: reset it first"
        if not self.link_fresh(now):
            return False, "drone link lost"
        esp_state = self._drone.get("esp_state")
        if esp_state != "LINK_FAILSAFE":
            return False, f"the ESP is in {esp_state}, not LINK_FAILSAFE"
        if self.resume_pending:
            return False, "a resume is already pending"
        self.resume_pending = True
        self._resume_t = now
        self._neutral_since = None  # the window starts with the next send
        return True, (
            f"RESUME_REQUEST goes out after {RESUME_WINDOW:.1f} s of neutral setpoints"
        )

    # ---------- ARM (operator only, decision A4) ----------
    def arm_allowed(self, mask, now):
        """May the operator's ~/arm_control send ARM now? Returns (ok, why).
        Only when the node is active, not in E-stop, the link is fresh, the ESP
        reports SESSION_IDLE (LINK_FAILSAFE needs RESUME, STREAMING is already
        armed), the FC link is not reported lost (#179 D2) and the mask is one
        of 8, 11, 15. The guard never asks for ARM itself (REQ-SAF-023)."""
        if not _is_mask(mask):
            return False, f"mask must be 8, 11 or 15, got {mask!r}"
        if not self.active:
            return False, "not active"
        if self.estop:
            return False, "E-stop latched: reset it first"
        if not self.link_fresh(now):
            return False, "drone link lost"
        if self._drone.get("fc_link_ok") is False:
            return False, "the ESP reports the FC link lost"
        esp_state = self._drone.get("esp_state")
        if esp_state != "SESSION_IDLE":
            return False, f"the ESP is in {esp_state}, not SESSION_IDLE"
        return True, f"ARM_CONTROL_REQUEST mask {mask}"

    def cancel_resume(self):
        self.resume_pending = False
        self._resume_t = None

    def note_sent(self, command, now):
        """The node tells the guard what it just handed to the backend, so the
        resume window counts only setpoints really sent. A non-neutral command
        or a gap of RESUME_MAX_GAP or more restarts the window."""
        neutral = command is not None and tuple(command) == ZERO
        gap_ok = (
            self._last_sent_t is not None
            and 0 <= now - self._last_sent_t < RESUME_MAX_GAP
        )
        if not neutral:
            self._neutral_since = None
        elif self._neutral_since is None or not gap_ok:
            self._neutral_since = now
        self._last_sent_t = now

    def resume_due(self, now):
        """True once, when the pending resume may be sent now. A pending resume
        whose conditions no longer hold (not active, E-stop, link lost, the
        drone left LINK_FAILSAFE) or that waited longer than RESUME_TIMEOUT is
        dropped: the operator has to ask again."""
        if not self.resume_pending:
            return False
        still_valid = (
            self.active
            and not self.estop
            and self.link_fresh(now)
            and self._drone.get("esp_state") == "LINK_FAILSAFE"
            and 0 <= now - self._resume_t <= RESUME_TIMEOUT
        )
        if not still_valid:
            self.cancel_resume()
            return False
        window_full = (
            self._neutral_since is not None
            and now - self._neutral_since >= RESUME_WINDOW
            and self._last_sent_t is not None
            and 0 <= now - self._last_sent_t < RESUME_MAX_GAP
        )
        if window_full:
            self.cancel_resume()
            return True
        return False

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
        and a known mask: the same gate mission_node applies (§3.2). Also
        closed while the ESP reports the FC link lost (#179): the ESP keeps
        STREAMING then, but nothing it sends reaches the FC."""
        if not self.link_fresh(now):
            return False
        drone = self._drone
        return (
            drone.get("esp_state") == "STREAMING"
            and drone.get("override_active") is True
            and _is_mask(drone.get("mask"))
            and drone.get("fc_link_ok") is not False
        )

    def flight_state(self, now):
        """The /flight/state fields mission_node expects ("04 Mission Node" §3.2),
        plus active / estop / estop_reason / resume_pending for display and
        failsafe_output from the ESP backend (EVENT 0x0104 or LINK_FAILSAFE:
        the ESP is sending its ADR-004 output, whatever esp_state says) and
        fc_link_ok (#179: true / false from EVENT 0x0202 / 0x0201, null when
        unknown).

        link_ok: the ground can control the drone now, i.e. the link is fresh,
        the node is ACTIVE, not in E-stop, and the ESP does not report the FC
        link lost (decision K1, 2026-11-10). If not, mission_node goes IDLE.
        The other fields come from the last valid drone state; with no fresh
        state they fall back to the safe values (no override, no mask).
        """
        fresh = self.link_fresh(now)
        drone = self._drone if fresh else {}
        return {
            "link_ok": (
                fresh
                and self.active
                and not self.estop
                and drone.get("fc_link_ok") is not False
            ),
            "esp_state": drone.get("esp_state"),  # None: nothing fresh to report
            "override_active": drone.get("override_active") is True,
            "mask": drone.get("mask"),
            "airborne": drone.get("airborne") is True,
            "failsafe_output": drone.get("failsafe_output") is True,
            "fc_link_ok": (
                drone.get("fc_link_ok")
                if isinstance(drone.get("fc_link_ok"), bool)
                else None
            ),
            "active": self.active,
            "estop": self.estop,
            "estop_reason": self.estop_reason,
            "resume_pending": self.resume_pending,
        }
