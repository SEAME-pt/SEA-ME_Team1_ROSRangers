"""Send the recognised gesture from gestures.py to intent_node (ROS 2 topic).

Topic version of drone/tools/gesture/gesture_sender.py: same send_gesture()
call, same JSON packet, same keep-alive rule. Only the transport changed: the
packet goes out through a publish function that gesture_node.py sets with
set_publisher() (a std_msgs/String on /gesture/raw) instead of a UDP socket.

No ROS imports here, so the keep-alive rule can be unit-tested without ROS 2.

Use it from gestures.py, once per camera frame:

    from .gesture_sender import send_gesture

    # gesture_name: e.g. "INDEX_RIGHT", or None when no hand / unknown
    # palm_xy: optional [x, y] of the palm in 0..1, or None
    send_gesture(gesture_name, palm_xy)

It sends one packet immediately when the gesture CHANGES, and repeats it every
KEEPALIVE_SEC while it stays the same. The repeats are essential: intent_node
treats silence as "the recogniser died or the hand is gone".

"""

import json
import time

KEEPALIVE_SEC = 0.1  # 10 packets/s -> a new gesture is accepted in ~0.4 s

_publisher = {"publish": None}  # function taking the JSON text, set by the node
_last = {"gesture": "<unset>", "time": 0.0}


def set_publisher(publish):
    """publish(text) sends one JSON packet. None stops sending."""
    _publisher["publish"] = publish
    _last["gesture"] = "<unset>"  # the first packet after this goes out at once
    _last["time"] = 0.0


def send_gesture(gesture, xy=None):
    """Call every frame. Sends on change, and as a keep-alive while unchanged."""
    now = time.monotonic()
    changed = gesture != _last["gesture"]
    if not changed and now - _last["time"] < KEEPALIVE_SEC:
        return
    publish = _publisher["publish"]
    if publish is None:
        return  # no node yet: like nobody listening on UDP, not an error
    packet = {"gesture": gesture, "t": time.time(), "xy": xy}
    publish(json.dumps(packet))
    _last["gesture"] = gesture
    _last["time"] = now
