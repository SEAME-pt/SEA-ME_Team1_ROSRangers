"""Pretend inputs for testing mission_node without intent_node or a drone.

No ROS imports, so the unit tests can play the same script.
"""

import json

# (intent, reason, seconds). intent None = publish nothing (a dead intent_node).
# With the default fake flight state (override on, mask 11, airborne) the
# mission_node should go through the modes in the comments.
DEMO_SCRIPT = [
    ("NEUTRAL", "no_hand", 2.0),  # WAITING, zero
    ("ENGAGE", "ok", 2.0),  # ENGAGED (both open hands, shown on purpose)
    ("MOVE_RIGHT", "ok", 3.0),  # linear.y ramps to -0.3
    ("YAW_LEFT", "ok", 2.0),  # angular.z ramps to +0.349 rad/s
    ("CLIMB_UP", "ok", 2.0),  # zero under mask 11: axis_not_owned:z
    ("BANANA", "ok", 1.0),  # unknown intent -> NEUTRAL, zero, still ENGAGED
    ("LOCK_TOGGLE", "ok", 2.0),  # zero; LOCKED after 1 s (both fists held)
    ("MOVE_LEFT", "ok", 2.0),  # stays zero: locked
    ("LOCK_TOGGLE", "ok", 2.0),  # ENGAGED again after 1 s (both fists again)
    ("MOVE_LEFT", "ok", 2.0),  # linear.y ramps to +0.3
    ("NEUTRAL", "no_hand", 1.0),  # hands gone: zero at once, still ENGAGED
    ("NEUTRAL", "no_hand_long", 3.0),  # still ENGAGED and hovering: no land request
    ("MOVE_LEFT", "ok", 2.0),  # hands back: moves again, no new engage needed
    (None, "", 3.0),  # silence: intent_stale, intent_topic_lost; zero, ENGAGED
]

# One gesture per intent, as gesture_node names it (see intent_node's
# GESTURE_TO_INTENT, which has a left- and a right-hand form of most).
FAKE_GESTURE = {
    "ENGAGE": "L:OPEN_HAND_UP+R:OPEN_HAND_UP",
    "LOCK_TOGGLE": "L:CLOSED_HAND+R:CLOSED_HAND",
    "CLIMB_UP": "L:NONE+R:INDEX_UP",
    "DESCEND_ONLY": "L:NONE+R:INDEX_DOWN",
    "MOVE_FORWARD": "L:NONE+R:INDEX_MIDDLE_LEFT",
    "MOVE_BACKWARD": "L:NONE+R:INDEX_MIDDLE_RING_PINKY_LEFT",
    "MOVE_LEFT": "L:NONE+R:INDEX_LEFT",
    "MOVE_RIGHT": "L:NONE+R:THUMB_RIGHT",
    "YAW_LEFT": "L:NONE+R:THUMB_INDEX_LEFT",
    "YAW_RIGHT": "L:NONE+R:THUMB_INDEX_UP",
}


def script_duration(script=DEMO_SCRIPT):
    return sum(seconds for _, _, seconds in script)


def step_at(elapsed, script=DEMO_SCRIPT):
    """(index, intent, reason) of the step playing at `elapsed` seconds,
    or None once the script is over."""
    for index, (intent, reason, seconds) in enumerate(script):
        if elapsed < seconds:
            return index, intent, reason
        elapsed -= seconds
    return None


def intent_json(intent, reason, t=None):
    """A message in the same format as intent_node ("03 Intent Node" §4)."""
    return json.dumps(
        {
            "intent": intent,
            "gesture": FAKE_GESTURE.get(intent),
            "reason": reason,
            "t": t,
            "xy": None,
        },
        separators=(",", ":"),
    )


def flight_state_json(link_ok, esp_state, override_active, mask, airborne):
    """A /flight/state message ("04 Mission Node" §3.2)."""
    return json.dumps(
        {
            "link_ok": bool(link_ok),
            "esp_state": str(esp_state),
            "override_active": bool(override_active),
            "mask": int(mask),
            "airborne": bool(airborne),
        },
        separators=(",", ":"),
    )
