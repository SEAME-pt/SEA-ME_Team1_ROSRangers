"""Pretend inputs for testing mission_node without intent_node or a drone.

No ROS imports, so the unit tests can play the same script.
"""

import json

# (intent, reason, seconds). intent None = publish nothing (a dead intent_node).
# With the default fake flight state (override on, mask 11, airborne) the
# mission_node should go through the modes in the comments.
DEMO_SCRIPT = [
    ("NEUTRAL", "no_hand", 2.0),  # READY, zero
    ("HOLD_POSITION", "ok", 2.0),  # ENGAGED (deliberate open hand)
    ("MOVE_RIGHT", "ok", 3.0),  # linear.y ramps to -0.3
    ("YAW_LEFT", "ok", 2.0),  # angular.z ramps to +0.349 rad/s
    ("CLIMB_SLOWLY", "ok", 2.0),  # zero under mask 11: axis_not_owned:z
    ("BANANA", "ok", 1.0),  # unknown intent -> NEUTRAL, zero, still ENGAGED
    ("STOP_HOVER", "ok", 2.0),  # READY
    ("MOVE_LEFT", "ok", 2.0),  # stays zero: not engaged
    ("HOLD_POSITION", "ok", 2.0),  # ENGAGED again
    ("MOVE_LEFT", "ok", 2.0),  # linear.y ramps to +0.3
    ("NEUTRAL", "no_hand", 1.0),  # dead-man: zero at once, still ENGAGED
    ("HOVER_LAND_REQUEST", "no_hand_long", 3.0),  # LAND_REQUEST (airborne)
    ("HOLD_POSITION", "ok", 2.0),  # READY: operator back
    (None, "", 3.0),  # silence: intent_stale, then intent_topic_lost -> LAND_REQUEST
]

FAKE_GESTURE = {
    "HOLD_POSITION": "OPEN_HAND_UP",
    "STOP_HOVER": "CLOSED_HAND",
    "CLIMB_SLOWLY": "THUMB_UP",
    "DESCEND_SLOWLY_LAND_REQUEST": "THUMB_DOWN",
    "MOVE_LEFT": "INDEX_LEFT",
    "MOVE_RIGHT": "INDEX_RIGHT",
    "YAW_LEFT": "INDEX_MIDDLE_LEFT",
    "YAW_RIGHT": "INDEX_MIDDLE_RIGHT",
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
