"""Decision logic of mission_node, with NO ROS imports.

Keeping this separate means the safety rules can be unit-tested on any
machine (no ROS 2 needed) and the clock can be injected for tests.

Rules (docs: "04 Mission Node" §4-5):
  * Input freshness: the zero target goes out no later than `intent_timeout`
    s after the last intent (REQ-SAF-017), whatever the phase of the tick.
    After `intent_lost` s the reason becomes "intent_topic_lost" (still
    NEUTRAL). Before the first intent ever arrives it is "no_intent_yet".
  * Bad JSON or an unknown intent -> NEUTRAL. "Unknown" is never "keep moving".
  * Modes IDLE -> WAITING -> ENGAGED <-> LOCKED. Override off, link lost,
    ESP not STREAMING or stale flight state -> IDLE from any mode, then
    WAITING once the flight state is OK again.
  * ENGAGE (both open hands) engages from WAITING. LOCK_TOGGLE (both fists)
    locks and unlocks once it has been held for `lock_hold` s. Both count
    only from when the gesture STARTS: hands already up when the node starts,
    or held through IDLE, do not count, and a held lock gesture toggles once.
  * No hands, or no intents at all: zero (hover), the mode does not change.
    Nothing here asks for a landing; LOCKED has no timeout.
  * Only the axes in the override mask are commanded (8, 11 or 15).
  * Speeds ramp up at `max_accel` and are clamped to the limits and to a
    hard-coded ceiling. Slowing down and stopping are immediate.
  * The node never arms or disarms.
"""

import json
import math
import time

# ---------- Modes ----------
IDLE = "IDLE"
WAITING = "WAITING"
ENGAGED = "ENGAGED"
LOCKED = "LOCKED"

# ---------- Intents (published by intent_node, "03 Intent Node" §4) ----------
ENGAGE = "ENGAGE"
LOCK_TOGGLE = "LOCK_TOGGLE"
CLIMB_UP = "CLIMB_UP"
DESCEND = "DESCEND_ONLY"
MOVE_FORWARD = "MOVE_FORWARD"
MOVE_BACKWARD = "MOVE_BACKWARD"
MOVE_LEFT = "MOVE_LEFT"
MOVE_RIGHT = "MOVE_RIGHT"
YAW_LEFT = "YAW_LEFT"
YAW_RIGHT = "YAW_RIGHT"
NEUTRAL = "NEUTRAL"

# Direction of each intent per axis (vx, vy, vz, wz), in units of the limit.
# Body frame FLU (REP-103): x forward, y LEFT, z up, yaw + = counter-clockwise.
INTENT_DIRECTION = {
    ENGAGE: (0, 0, 0, 0),
    LOCK_TOGGLE: (0, 0, 0, 0),
    MOVE_FORWARD: (+1, 0, 0, 0),
    MOVE_BACKWARD: (-1, 0, 0, 0),
    MOVE_LEFT: (0, +1, 0, 0),
    MOVE_RIGHT: (0, -1, 0, 0),
    CLIMB_UP: (0, 0, +1, 0),
    DESCEND: (0, 0, -1, 0),
    YAW_LEFT: (0, 0, 0, +1),
    YAW_RIGHT: (0, 0, 0, -1),
    NEUTRAL: (0, 0, 0, 0),
}
KNOWN_INTENTS = frozenset(INTENT_DIRECTION)

# Reasons that say what the operator's hands are doing ("03 Intent Node" §4).
# Only these count when deciding whether a gesture just STARTED. A delivery
# gap, bad JSON, intent_node starting (no_packets_yet), a dead recogniser
# (no_packets) or a gesture still being confirmed (unconfirmed) never looks
# like "hands dropped, then shown again".
HAND_REASONS = frozenset({"ok", "no_hand", "no_hand_long", "unmapped_gesture"})

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
        lock_hold=1.0,
        tick_period=0.05,
        clock=time.monotonic,
    ):
        self.intent_timeout = max(0.0, float(intent_timeout))
        # tick() only runs every `tick_period` s: an intent that would go
        # stale before the next tick is treated as stale now, so the zero
        # target is never late.
        self.tick_period = max(0.0, float(tick_period))
        # Losing the topic can never be decided before it is stale. It only
        # changes the reason in /mission/state: the target is zero either way.
        self.intent_lost = max(self.intent_timeout, float(intent_lost))
        self.flight_state_timeout = max(0.0, float(flight_state_timeout))
        self.max_lateral = _limit(max_lateral_mps, CEILING_LINEAR_MPS)
        self.max_vertical = _limit(max_vertical_mps, CEILING_LINEAR_MPS)
        self.max_yaw = math.radians(_limit(max_yaw_rate_dps, CEILING_YAW_DPS))
        self.max_accel = max(0.0, float(max_accel_mps2))
        self.max_yaw_accel = math.radians(max(0.0, float(max_yaw_accel_dps2)))
        self.lock_hold = max(0.0, float(lock_hold))  # NaN -> 0: toggles at once
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
        self._prev_hand_intent = None  # intent of the last HAND_REASONS message
        self._lock_since = None  # start of the current LOCK_TOGGLE hold
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
            return NEUTRAL, "intent_topic_lost", age
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
        self.mode = mode
        self.mode_reason = reason

    def _update_mode(self, now, intent, reason, blocker):
        # ENGAGE and LOCK_TOGGLE count from when they START: the previous
        # message about the hands said something else. The first message ever
        # is no start either: the hands may have been up before this node
        # (re)started.
        started = None
        if reason in HAND_REASONS:
            if self._prev_hand_intent not in (None, intent):
                started = intent
            self._prev_hand_intent = intent
        # The lock gesture must then be held without a break (a delivery gap
        # is NEUTRAL, so it is a break too). It toggles once per hold.
        if intent != LOCK_TOGGLE:
            self._lock_since = None
        elif started == LOCK_TOGGLE:
            self._lock_since = now
        held = self._lock_since is not None and now - self._lock_since >= self.lock_hold

        if blocker is not None:
            self._set_mode(IDLE, blocker)
            return
        if self.mode == IDLE:
            self._set_mode(WAITING, "override_on")

        if self.mode == WAITING and started == ENGAGE:
            self._set_mode(ENGAGED, "engage")
        elif self.mode == ENGAGED and held:
            self._lock_since = None
            self._set_mode(LOCKED, "lock")
        elif self.mode == LOCKED and held:
            self._lock_since = None
            self._set_mode(ENGAGED, "unlock")

    def _raw_target(self, intent):
        """Velocity target before masking, ramping and clamping."""
        if self.mode != ENGAGED:
            return [0.0, 0.0, 0.0, 0.0]
        d = INTENT_DIRECTION[intent]
        return [
            d[0] * self.max_lateral,
            d[1] * self.max_lateral,
            d[2] * self.max_vertical,
            d[3] * self.max_yaw,
        ]

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

        self._update_mode(now, intent, reason, blocker)

        target = self._raw_target(intent)

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
            # Kept for the flight controller telemetry trigger (not built
            # yet). Nothing here asks for a landing.
            "land_request": False,
            "limited": limited,
            "intent_age_ms": int(age * 1000),
        }
        return {"velocity": list(self._velocity), "state": state}
