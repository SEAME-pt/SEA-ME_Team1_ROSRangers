"""Decision logic of mission_node, with NO ROS imports.

Keeping this separate means the safety rules can be unit-tested on any
machine (no ROS 2 needed) and the clock can be injected for tests.

Rules (docs: "04 Mission Node" §4-5):
  * Input freshness: the zero target goes out no later than `intent_timeout`
    s after the last intent (REQ-SAF-017), whatever the phase of the tick;
    no intent for `intent_lost` s -> HOVER_LAND_REQUEST (reason
    "intent_topic_lost"). Before the first intent ever arrives it is only
    NEUTRAL ("no_intent_yet").
  * Bad JSON or an unknown intent -> NEUTRAL. "Unknown" is never "keep moving".
  * Modes IDLE -> READY -> ENGAGED -> LAND_REQUEST. Override off, link lost,
    ESP not STREAMING or stale flight state -> IDLE from any mode.
  * Engaging needs a deliberate HOLD_POSITION that starts while READY. A
    hand already held when the node starts does not count.
  * Only the axes in the override mask are commanded (8, 11 or 15).
  * Speeds ramp up at `max_accel` and are clamped to the limits and to a
    hard-coded ceiling. Slowing down and stopping are immediate.
  * Land requests are ignored on the ground, and intent_node's start-up
    HOVER_LAND_REQUEST ("no_packets_yet") is only NEUTRAL. The node never arms or disarms.
"""

import json
import math
import time

# ---------- Modes ----------
IDLE = "IDLE"
READY = "READY"
ENGAGED = "ENGAGED"
LAND_REQUEST = "LAND_REQUEST"

# ---------- Intents (published by intent_node, "03 Intent Node" §4) ----------
HOLD_POSITION = "HOLD_POSITION"
STOP_HOVER = "STOP_HOVER"
CLIMB_SLOWLY = "CLIMB_SLOWLY"
DESCEND = "DESCEND_SLOWLY_LAND_REQUEST"
MOVE_LEFT = "MOVE_LEFT"
MOVE_RIGHT = "MOVE_RIGHT"
YAW_LEFT = "YAW_LEFT"
YAW_RIGHT = "YAW_RIGHT"
NEUTRAL = "NEUTRAL"
HOVER_LAND_REQUEST = "HOVER_LAND_REQUEST"

# Direction of each intent per axis (vx, vy, vz, wz), in units of the limit.
# Body frame FLU (REP-103): x forward, y LEFT, z up, yaw + = counter-clockwise.
INTENT_DIRECTION = {
    HOLD_POSITION: (0, 0, 0, 0),
    STOP_HOVER: (0, 0, 0, 0),
    MOVE_LEFT: (0, +1, 0, 0),
    MOVE_RIGHT: (0, -1, 0, 0),
    CLIMB_SLOWLY: (0, 0, +1, 0),
    DESCEND: (0, 0, -1, 0),
    YAW_LEFT: (0, 0, 0, +1),
    YAW_RIGHT: (0, 0, 0, -1),
    NEUTRAL: (0, 0, 0, 0),
    HOVER_LAND_REQUEST: (0, 0, 0, 0),
}
KNOWN_INTENTS = frozenset(INTENT_DIRECTION)

# Reasons this node makes up itself when /gesture/intent is late or missing.
WATCHDOG_REASONS = frozenset({"no_intent_yet", "intent_stale", "intent_topic_lost"})

# ---------- Axes the ground owns per override mask ("04 Mission Node" §5.3) ----------
AXES = ("x", "y", "z", "yaw")
MASK_AXES = {
    8: {"yaw"},
    11: {"x", "y", "yaw"},
    15: {"x", "y", "z", "yaw"},
}

# ---------- Hard ceilings: no parameter can go above these ----------
CEILING_LINEAR_MPS = 0.5
CEILING_YAW_DPS = 45.0

MAX_TICK_DT = 0.2  # a stalled timer must not turn into one huge ramp step


def _is_true(value):
    """Only a real JSON true counts. "true", 1 or missing are all False."""
    return value is True


def parse_intent(text):
    """Parse a /gesture/intent JSON string.

    Returns (intent, reason, gesture, problem). `problem` is None for a good
    message, "bad_json" or "unknown_intent" otherwise (intent is then NEUTRAL).
    """
    try:
        data = json.loads(text)
    except (TypeError, ValueError, RecursionError):  # deep nesting: RecursionError
        return NEUTRAL, "bad_json", None, "bad_json"
    if not isinstance(data, dict):
        return NEUTRAL, "bad_json", None, "bad_json"

    intent = data.get("intent")
    intent = intent.strip().upper() if isinstance(intent, str) else None
    gesture = data.get("gesture") if isinstance(data.get("gesture"), str) else None
    reason = data.get("reason") if isinstance(data.get("reason"), str) else ""
    if intent not in KNOWN_INTENTS:
        return NEUTRAL, "unknown_intent", gesture, "unknown_intent"
    if intent == HOVER_LAND_REQUEST and reason == "no_packets_yet":
        # intent_node at start-up: "no recogniser yet", not a real land request.
        return NEUTRAL, reason, gesture, None
    return intent, reason, gesture, None


def parse_flight_state(text):
    """Parse a /flight/state JSON string into a clean dict, or None if bad."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError, RecursionError):  # deep nesting: RecursionError
        return None
    if not isinstance(data, dict):
        return None
    mask = data.get("mask")
    if isinstance(mask, bool) or not isinstance(mask, int):
        mask = None
    esp_state = data.get("esp_state")
    return {
        "link_ok": _is_true(data.get("link_ok")),
        "esp_state": esp_state.strip().upper() if isinstance(esp_state, str) else None,
        "override_active": _is_true(data.get("override_active")),
        "mask": mask,
        "airborne": _is_true(data.get("airborne")),
    }


def _limit(value, ceiling):
    """A safe, finite, non-negative limit that never exceeds the ceiling."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return min(max(0.0, value), ceiling)


def ramp(current, target, step):
    """Move `current` towards `target`.

    Speeding up is limited to `step` per tick. Slowing down (towards zero) is
    immediate, because stopping is the safe direction. A change of sign goes
    through zero first, then ramps up again. A step that is not a positive
    number (NaN: an infinite max_accel times a zero dt) does not speed up.
    """
    if not step > 0.0:
        step = 0.0
    if target == 0.0 or current * target < 0.0:
        current = 0.0
    if abs(target) <= abs(current):
        return target
    return current + math.copysign(min(step, abs(target) - abs(current)), target)


class MissionLogic:
    def __init__(
        self,
        intent_timeout=0.25,
        intent_lost=2.0,
        flight_state_timeout=0.5,
        max_lateral_mps=0.3,
        max_vertical_mps=0.2,
        max_yaw_rate_dps=20.0,
        max_accel_mps2=0.5,
        max_yaw_accel_dps2=40.0,
        land_hover_sec=2.0,
        tick_period=0.05,
        clock=time.monotonic,
    ):
        self.intent_timeout = max(0.0, float(intent_timeout))
        # tick() only runs every `tick_period` s: an intent that would go
        # stale before the next tick is treated as stale now, so the zero
        # target is never late.
        self.tick_period = max(0.0, float(tick_period))
        # Losing the topic can never be decided before it is stale.
        self.intent_lost = max(self.intent_timeout, float(intent_lost))
        self.flight_state_timeout = max(0.0, float(flight_state_timeout))
        self.max_lateral = _limit(max_lateral_mps, CEILING_LINEAR_MPS)
        self.max_vertical = _limit(max_vertical_mps, CEILING_LINEAR_MPS)
        self.max_yaw = math.radians(_limit(max_yaw_rate_dps, CEILING_YAW_DPS))
        self.max_accel = max(0.0, float(max_accel_mps2))
        self.max_yaw_accel = math.radians(max(0.0, float(max_yaw_accel_dps2)))
        self.land_hover_sec = max(0.0, float(land_hover_sec))
        self._clock = clock

        # Requested values above the ceilings are reported once by the node.
        self.clamped_params = [
            name
            for name, asked, ceiling in (
                ("max_lateral_mps", max_lateral_mps, CEILING_LINEAR_MPS),
                ("max_vertical_mps", max_vertical_mps, CEILING_LINEAR_MPS),
                ("max_yaw_rate_dps", max_yaw_rate_dps, CEILING_YAW_DPS),
            )
            if _limit(asked, float("inf")) > ceiling
        ]

        self._start = clock()
        # Latest intent message
        self._intent_time = None
        self._intent = NEUTRAL
        self._intent_reason = "no_intent_yet"
        self._gesture = None
        # Latest flight state message
        self._flight = None
        self._flight_time = None
        # Counters for the health report
        self.bad_json = 0
        self.unknown_intents = 0
        self.bad_flight_states = 0
        # State machine
        self.mode = IDLE
        self.mode_reason = "no_flight_state"
        self._prev_fresh_intent = None  # last intent from a fresh message
        self._neutral_since = None
        self._land_since = None
        # Ramp
        self._last_tick = None
        self._velocity = [0.0, 0.0, 0.0, 0.0]  # vx, vy, vz (m/s), wz (rad/s)

    # ---------- Inputs ----------
    def on_intent(self, text):
        """Feed one /gesture/intent message (the JSON string)."""
        intent, reason, gesture, problem = parse_intent(text)
        if problem == "bad_json":
            self.bad_json += 1
        elif problem == "unknown_intent":
            self.unknown_intents += 1
        self._intent_time = self._clock()
        self._intent, self._intent_reason, self._gesture = intent, reason, gesture
        return problem

    def on_flight_state(self, text):
        """Feed one /flight/state message. A bad one is ignored (and counted),
        so the last good state goes stale and the node falls back to IDLE."""
        state = parse_flight_state(text)
        if state is None:
            self.bad_flight_states += 1
            return False
        self._flight = state
        self._flight_time = self._clock()
        return True

    # ---------- Helpers ----------
    def _effective_intent(self, now):
        """(intent, reason, age) after the freshness check."""
        if self._intent_time is None:
            # Never heard from intent_node: "not started", not "crashed".
            return NEUTRAL, "no_intent_yet", now - self._start
        age = now - self._intent_time
        if age > self.intent_lost:
            return HOVER_LAND_REQUEST, "intent_topic_lost", age
        if age + self.tick_period > self.intent_timeout:
            return NEUTRAL, "intent_stale", age
        return self._intent, self._intent_reason, age

    def _flight_blocker(self, now):
        """Why the ground may NOT command right now, or None if it may."""
        if self._flight is None:
            return "no_flight_state"
        if now - self._flight_time > self.flight_state_timeout:
            return "flight_state_stale"
        if not self._flight["link_ok"]:
            return "link_lost"
        if self._flight["esp_state"] != "STREAMING":
            return "not_streaming"
        if not self._flight["override_active"]:
            return "override_off"
        if self._flight["mask"] not in MASK_AXES:
            return "bad_mask"
        return None

    def _set_mode(self, mode, reason):
        if mode != self.mode:
            self._neutral_since = None
            self._land_since = None
        self.mode = mode
        self.mode_reason = reason

    def _update_mode(self, now, intent, reason, blocker, airborne):
        # Engaging needs HOLD_POSITION to START. Only fresh messages count, so
        # a short delivery gap (synthetic NEUTRAL) never looks like a re-show.
        # The first message ever is no start either: the hand may have been
        # held before this node (re)started.
        fresh = reason not in WATCHDOG_REASONS
        new_hold = (
            fresh
            and intent == HOLD_POSITION
            and self._prev_fresh_intent not in (None, HOLD_POSITION)
        )
        if fresh:
            self._prev_fresh_intent = intent

        if blocker is not None:
            self._set_mode(IDLE, blocker)
            return
        if self.mode == IDLE:
            self._set_mode(READY, "override_on")

        if self.mode == READY:
            if intent == HOVER_LAND_REQUEST and airborne:
                self._set_mode(LAND_REQUEST, reason)
            elif new_hold:
                self._set_mode(ENGAGED, "hold_position")
        elif self.mode == ENGAGED:
            if intent == STOP_HOVER:
                self._set_mode(READY, "stop_hover")
            elif intent == HOVER_LAND_REQUEST:
                # On the ground there is nothing to land: just disengage.
                if airborne:
                    self._set_mode(LAND_REQUEST, reason)
                else:
                    self._set_mode(READY, "land_request_on_ground")
            elif intent == NEUTRAL:
                if self._neutral_since is None:
                    self._neutral_since = now
                elif now - self._neutral_since > self.intent_lost:
                    self._set_mode(READY, "neutral_too_long")
            else:
                self._neutral_since = None
        elif self.mode == LAND_REQUEST:
            if intent == HOLD_POSITION:
                self._set_mode(READY, "operator_back")
            elif not airborne:
                self._set_mode(READY, "landed")

    def _raw_target(self, now, intent, mask):
        """Velocity target before masking, ramping and clamping."""
        if self.mode == ENGAGED:
            d = INTENT_DIRECTION[intent]
            return [
                d[0] * self.max_lateral,
                d[1] * self.max_lateral,
                d[2] * self.max_vertical,
                d[3] * self.max_yaw,
            ]
        if self.mode == LAND_REQUEST and mask == 15:
            # Throttle is ours: hover first, then descend slowly.
            if self._land_since is None:
                self._land_since = now
            if now - self._land_since >= self.land_hover_sec:
                return [0.0, 0.0, -self.max_vertical, 0.0]
        return [0.0, 0.0, 0.0, 0.0]

    # ---------- One 20 Hz tick ----------
    def tick(self):
        """Run one cycle. Returns {"velocity": [vx, vy, vz, wz], "state": {...}}.

        velocity is in m/s and rad/s, body frame FLU.
        """
        now = self._clock()
        dt = 0.0 if self._last_tick is None else now - self._last_tick
        dt = min(max(dt, 0.0), MAX_TICK_DT)
        self._last_tick = now

        intent, reason, age = self._effective_intent(now)
        blocker = self._flight_blocker(now)
        flight = self._flight or {}
        airborne = blocker is None and flight.get("airborne", False)
        mask = flight.get("mask") if blocker is None else None

        self._update_mode(now, intent, reason, blocker, airborne)

        target = self._raw_target(now, intent, mask)

        # Axis masking: zero what the ground does not own.
        owned = MASK_AXES.get(mask, set())
        limited = []
        for i, axis in enumerate(AXES):
            if axis not in owned:
                if target[i] != 0.0:
                    limited.append(f"axis_not_owned:{axis}")
                target[i] = 0.0

        # Fault paths stop at once: the target is already zero and ramp()
        # never slows down gradually, so this holds by construction.
        steps = (self.max_accel * dt,) * 3 + (self.max_yaw_accel * dt,)
        ceilings = (self.max_lateral, self.max_lateral, self.max_vertical, self.max_yaw)
        for i in range(4):
            v = ramp(self._velocity[i], target[i], steps[i])
            self._velocity[i] = max(-ceilings[i], min(ceilings[i], v))
            if self._velocity[i] != target[i]:
                limited.append(f"ramping:{AXES[i]}")

        state = {
            "mode": self.mode,
            "mode_reason": self.mode_reason,
            "intent": intent,
            "reason": reason,
            "gesture": self._gesture if self._intent_time is not None else None,
            "mask": mask,
            "airborne": bool(airborne),
            "land_request": self.mode == LAND_REQUEST,
            "limited": limited,
            "intent_age_ms": int(age * 1000),
        }
        return {"velocity": list(self._velocity), "state": state}
