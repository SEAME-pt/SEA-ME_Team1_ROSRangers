"""Decision logic of intent_node, with NO ROS imports.

Keeping this separate means the safety rules can be unit-tested on any
machine (no ROS 2 needed) and the clock can be injected for tests.

Rules (see "Gesture Pipeline" safety logic):
  * A gesture must be seen in `stable_packets` consecutive packets before it
    is accepted.
  * No hand for more than `neutral_timeout` seconds -> NEUTRAL, and the
    accepted gesture is forgotten (an old gesture is never replayed).
  * No hand / no packets for more than `stale_timeout` seconds
    -> HOVER_LAND_REQUEST.
  * Nothing received yet -> HOVER_LAND_REQUEST (reason "no_packets_yet").
  * Unknown gestures are NEUTRAL. "No data" is never "keep moving".
"""

import math
import time

INTENT_NEUTRAL = "NEUTRAL"
INTENT_HOVER_LAND = "HOVER_LAND_REQUEST"

GESTURE_TO_INTENT = {
    "OPEN_HAND_UP": "HOLD_POSITION",
    "CLOSED_HAND": "STOP_HOVER",
    "THUMB_UP": "CLIMB_SLOWLY",
    "THUMB_DOWN": "DESCEND_SLOWLY_LAND_REQUEST",
    "INDEX_LEFT": "MOVE_LEFT",
    "INDEX_RIGHT": "MOVE_RIGHT",
    "INDEX_MIDDLE_LEFT": "YAW_LEFT",
    "INDEX_MIDDLE_RIGHT": "YAW_RIGHT",
}

# Values that mean "no hand / nothing recognised".
NO_HAND_TOKENS = {"", "NONE", "NO_HAND", "UNKNOWN", "NEUTRAL"}


def normalize_gesture(value):
    """Return an upper-case gesture name, or None when there is no hand."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in NO_HAND_TOKENS:
        return None
    return text


def clean_number(value):
    """Return a finite float, or None (rejects bool, NaN, inf, strings)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def clean_xy(value):
    """Return [x, y] as finite floats, or None."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x = clean_number(value[0])
    y = clean_number(value[1])
    if x is None or y is None:
        return None
    return [x, y]


class GestureFilter:
    def __init__(
        self,
        stable_packets=5,
        neutral_timeout=0.3,
        stale_timeout=2.0,
        clock=time.monotonic,
    ):
        self.stable_packets = max(1, int(stable_packets))
        self.neutral_timeout = max(0.0, float(neutral_timeout))
        # The stale timeout can never be shorter than the neutral timeout.
        self.stale_timeout = max(self.neutral_timeout, float(stale_timeout))
        self._clock = clock

        self._last_packet = None  # time of the last packet of any kind
        self._last_hand = clock()  # time of the last packet WITH a hand
        self._candidate = None  # gesture currently being counted
        self._count = 0  # consecutive packets of the candidate
        self._accepted = None  # gesture that passed the stability check
        self._t = None
        self._xy = None

    def _expire(self, now):
        """Forget the accepted gesture once the hand has been gone too long."""
        if now - self._last_hand > self.neutral_timeout:
            self._candidate = None
            self._count = 0
            self._accepted = None

    def update(self, packet):
        """Feed one decoded datagram (a dict) into the filter."""
        now = self._clock()
        self._expire(now)  # must happen BEFORE the new packet counts
        self._last_packet = now
        self._t = clean_number(packet.get("t"))
        self._xy = clean_xy(packet.get("xy"))

        gesture = normalize_gesture(packet.get("gesture"))
        if gesture is None:
            # Noise must not build up across a gap, but a short dropout
            # (< neutral_timeout) keeps the accepted gesture.
            self._candidate = None
            self._count = 0
            return

        self._last_hand = now
        if gesture == self._candidate:
            self._count += 1
        else:
            self._candidate = gesture
            self._count = 1
        if self._count >= self.stable_packets:
            self._accepted = gesture

    def decide(self):
        """Return the intent to publish right now, as a dict."""
        now = self._clock()
        self._expire(now)

        def result(intent, gesture, reason):
            return {
                "intent": intent,
                "gesture": gesture,
                "reason": reason,
                "t": self._t,
                "xy": self._xy,
            }

        if self._last_packet is None:
            return result(INTENT_HOVER_LAND, None, "no_packets_yet")
        if now - self._last_packet > self.stale_timeout:
            return result(INTENT_HOVER_LAND, None, "no_packets")
        hand_age = now - self._last_hand
        if hand_age > self.stale_timeout:
            return result(INTENT_HOVER_LAND, None, "no_hand_long")
        if hand_age > self.neutral_timeout:
            return result(INTENT_NEUTRAL, None, "no_hand")
        if self._accepted is None:
            return result(INTENT_NEUTRAL, None, "unconfirmed")
        intent = GESTURE_TO_INTENT.get(self._accepted)
        if intent is None:
            return result(INTENT_NEUTRAL, self._accepted, "unmapped_gesture")
        return result(intent, self._accepted, "ok")
