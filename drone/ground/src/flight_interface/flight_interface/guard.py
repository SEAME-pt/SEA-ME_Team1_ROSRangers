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
    as mission_node, and the FC link reported alive (fc_link_ok true, #179).
    Axes outside the mask are zero (mission_node's REQ-SAF-018, applied again
    here), as in the PX4 bridge.
  * Same hard ceilings as mission_node, applied again here: the horizontal
    speed as a whole (so a diagonal is not faster), the vertical speed and the
    yaw rate. Non-finite values become 0.
  * The guard never asks for ARM (REQ-SAF-023). request_arm() only holds
    the operator's ~/arm_control (decision A4) until it may go out.
  * ARM and RESUME are operator actions (decisions A3, A4). request_arm() and
    request_resume() only hold a pending request, and request_due() says when
    the node may send it: after NEUTRAL_WINDOW (1.5 s) of neutral setpoints
    with no gap of NEUTRAL_MAX_GAP or more, while the drone still reports the
    state the request needs (SESSION_IDLE for ARM, LINK_FAILSAFE for RESUME)
    in the same session. The ESP refuses both until the FC has acknowledged
    RC for 1 s (result 4, #188), and after a failsafe RC flows only while the
    ground streams, so the node streams first (K6 as changed by the safety
    pilot on PR #191, 2026-11-13). A request still pending after
    REQUEST_TIMEOUT (3 s) is dropped. The guard never starts one by itself.
  * The FC link must be reported alive (fc_link_ok true) for ARM, RESUME,
    a non-zero setpoint and link_ok. Unknown (null) counts as not alive
    (decision E6.1): the ESP backend knows it within about 0.4 s of the
    first FC_HEALTH, and the ESP refuses ARM and RESUME while the FC is
    silent anyway (result 4).

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

# ARM and RESUME (decision K6, changed by the safety pilot on PR #191,
# 2026-11-13): the ESP refuses both until the FC has acknowledged RC for 1 s
# (result 4, Protocol Spec v1.4.1, #188), and RESUME also needs >= 500 ms of
# mode-0 setpoints with no gap of T_fresh (150 ms). The ground holds the
# operator's request for a 1.5 s window and allows a smaller gap, so jitter on
# the way does not make the ESP refuse it.
NEUTRAL_WINDOW = 1.5
NEUTRAL_MAX_GAP = 0.1
# A pending request that never became due is dropped: the operator asks again.
REQUEST_TIMEOUT = 3.0
ARM, RESUME = "arm", "resume"

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

        # The operator's pending ARM or RESUME (decisions A3, A4, K6): None or
        # {"kind": ARM | RESUME, "t": when asked, "session": the drone's
        # session_id then, "mask": the ARM mask}.
        self._pending = None
        self._last_sent_t = None  # last setpoint handed to the backend
        self._neutral_since = None  # start of the current run of neutral sends

    # ---------- lifecycle and E-stop ----------
    def activate(self):
        """Start sending. The target is dropped: a fresh one is needed first."""
        self.active = True
        self._drop_target()
        self.cancel_request()

    def deactivate(self):
        self.active = False
        self._drop_target()
        self.cancel_request()

    def trigger_estop(self, reason):
        """Latched until reset_estop(). Nothing is sent while it holds."""
        self.estop = True
        self.estop_reason = str(reason) or "estop"
        self._drop_target()
        self.cancel_request()

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
        has esp_state, override_active, mask, airborne (and from the ESP
        backend failsafe_output, fc_link_ok, armed, session_id,
        esp_rx_rejected); anything else is kept but not used. A state with an
        unknown esp_state is not a valid state. `now` is when the state was
        received; a state older than the one already held is ignored, so a late
        frame can never make the link look fresher than it is."""
        if not isinstance(state, dict) or state.get("esp_state") not in ESP_STATES:
            return
        if not _finite(now) or (self._drone_t is not None and now < self._drone_t):
            return
        self._drone = dict(state)
        self._drone_t = now

    # ---------- ARM and RESUME (operator only, decisions A3, A4, K6) ----------
    def _refusal(self, now, needs):
        """Why an operator request cannot be held now, or None. `needs` is the
        esp_state the request needs."""
        if not self.active:
            return "not active"
        if self.estop:
            return "E-stop latched: reset it first"
        if not self.link_fresh(now):
            return "drone link lost"
        fc_link_ok = self._drone.get("fc_link_ok")
        if fc_link_ok is False:
            return "the ESP reports the FC link lost"
        if fc_link_ok is not True:  # E6.1: unknown is not OK
            return "the FC link state is not known yet (no FC_HEALTH)"
        esp_state = self._drone.get("esp_state")
        if esp_state != needs:
            return f"the ESP is in {esp_state}, not {needs}"
        if self._pending is not None:
            return f"an operator {self._pending['kind'].upper()} is already pending"
        return None

    def _hold(self, kind, now, mask=None):
        self._pending = {
            "kind": kind,
            "t": now,
            "session": self._drone.get("session_id"),
            "mask": mask,
        }
        self._neutral_since = None  # the window starts with the next send

    def request_resume(self, now):
        """The operator asks to resume after a link failsafe. Returns (ok, why).

        Refused unless the node is active, not in E-stop, the link is fresh,
        the FC link is reported alive and the drone reports LINK_FAILSAFE.
        When accepted, the request is only pending: request_due() says when
        RESUME_REQUEST may go out."""
        why = self._refusal(now, "LINK_FAILSAFE")
        if why is not None:
            return False, why
        self._hold(RESUME, now)
        return True, (
            f"RESUME_REQUEST goes out after {NEUTRAL_WINDOW:.1f} s of neutral setpoints"
        )

    def request_arm(self, mask, now):
        """The operator's ~/arm_control. Returns (ok, why). Refused unless the
        mask is 8, 11 or 15, the node is active, not in E-stop, the link is
        fresh, the FC link is reported alive (#179 D2, E6.1) and the ESP
        reports SESSION_IDLE (LINK_FAILSAFE needs RESUME, STREAMING is already
        armed). When accepted, the request is only pending: request_due()
        says when ARM_CONTROL_REQUEST may go out. The guard never asks for
        ARM itself (REQ-SAF-023)."""
        if not _is_mask(mask):
            return False, f"mask must be 8, 11 or 15, got {mask!r}"
        why = self._refusal(now, "SESSION_IDLE")
        if why is not None:
            return False, why
        self._hold(ARM, now, mask)
        return True, (
            f"ARM_CONTROL_REQUEST mask {mask} goes out after "
            f"{NEUTRAL_WINDOW:.1f} s of neutral setpoints"
        )

    @property
    def resume_pending(self):
        return self._pending is not None and self._pending["kind"] == RESUME

    @property
    def arm_pending(self):
        return self._pending is not None and self._pending["kind"] == ARM

    def cancel_request(self):
        self._pending = None

    def note_sent(self, command, now):
        """The node tells the guard what it just handed to the backend, so the
        neutral window counts only setpoints really sent. A non-neutral
        command or a gap of NEUTRAL_MAX_GAP or more restarts the window."""
        neutral = command is not None and tuple(command) == ZERO
        gap_ok = (
            self._last_sent_t is not None
            and 0 <= now - self._last_sent_t < NEUTRAL_MAX_GAP
        )
        if not neutral:
            self._neutral_since = None
        elif self._neutral_since is None or not gap_ok:
            self._neutral_since = now
        self._last_sent_t = now

    def request_due(self, now):
        """The pending request that may be sent now, at most once: None,
        (ARM, mask) or (RESUME, None). A pending request whose conditions no
        longer hold (not active, E-stop, link lost, FC link not alive, the
        drone left the state it needs, a new session) or that waited longer
        than REQUEST_TIMEOUT is dropped: the operator has to ask again."""
        pending = self._pending
        if pending is None:
            return None
        needs = "SESSION_IDLE" if pending["kind"] == ARM else "LINK_FAILSAFE"
        still_valid = (
            self.active
            and not self.estop
            and self.link_fresh(now)
            and self._drone.get("esp_state") == needs
            and self._drone.get("fc_link_ok") is True
            and self._drone.get("session_id") == pending["session"]
            and 0 <= now - pending["t"] <= REQUEST_TIMEOUT
        )
        if not still_valid:
            self.cancel_request()
            return None
        window_full = (
            self._neutral_since is not None
            and now - self._neutral_since >= NEUTRAL_WINDOW
            and self._last_sent_t is not None
            and 0 <= now - self._last_sent_t < NEUTRAL_MAX_GAP
        )
        if not window_full:
            return None
        self.cancel_request()
        return pending["kind"], pending["mask"]

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
        possible (STREAMING, override on, a known mask, FC link alive).
        Otherwise the target, masked to the owned axes and then clamped.
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
        closed unless the FC link is reported alive (#179, E6.1): with the FC
        silent the ESP keeps STREAMING, but nothing it sends reaches the FC."""
        if not self.link_fresh(now):
            return False
        drone = self._drone
        return (
            drone.get("esp_state") == "STREAMING"
            and drone.get("override_active") is True
            and _is_mask(drone.get("mask"))
            and drone.get("fc_link_ok") is True
        )

    def flight_state(self, now):
        """The /flight/state fields mission_node expects ("04 Mission Node" §3.2),
        plus, for display and diagnostics: active, estop, estop_reason,
        resume_pending, arm_pending; failsafe_output (EVENT 0x0104 or
        LINK_FAILSAFE: the ESP is sending its ADR-004 output, whatever
        esp_state says); fc_link_ok (#179, E6/E6.1: true / false, null when
        unknown); armed (D1: the FC's armed flag from FC_HEALTH, true / false,
        null when unknown; airborne = armed for now) and esp_rx_rejected (the
        ESP's STATE.rx_rejected as a total, #149; null when unknown).

        link_ok: the ground can control the drone now, i.e. the link is fresh,
        the node is ACTIVE, not in E-stop, and the FC link is reported alive
        (decision K1, E6.1). If not, mission_node goes IDLE. The other fields
        come from the last valid drone state; with no fresh state they fall
        back to the safe values (no override, no mask, unknown).
        """
        fresh = self.link_fresh(now)
        drone = self._drone if fresh else {}

        def flag(name):  # true / false, None unless a real bool
            value = drone.get(name)
            return value if isinstance(value, bool) else None

        rejected = drone.get("esp_rx_rejected")
        return {
            "link_ok": (
                fresh
                and self.active
                and not self.estop
                and drone.get("fc_link_ok") is True
            ),
            "esp_state": drone.get("esp_state"),  # None: nothing fresh to report
            "override_active": drone.get("override_active") is True,
            "mask": drone.get("mask"),
            "airborne": drone.get("airborne") is True,
            "armed": flag("armed"),
            "failsafe_output": drone.get("failsafe_output") is True,
            "fc_link_ok": flag("fc_link_ok"),
            "esp_rx_rejected": (
                rejected
                if isinstance(rejected, int) and not isinstance(rejected, bool)
                else None
            ),
            "active": self.active,
            "estop": self.estop,
            "estop_reason": self.estop_reason,
            "resume_pending": self.resume_pending,
            "arm_pending": self.arm_pending,
        }
