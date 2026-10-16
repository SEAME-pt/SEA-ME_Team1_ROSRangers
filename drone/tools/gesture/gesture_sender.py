"""Send the recognised gesture from gestures.py to gesture_node (UDP, localhost).

Plain standard library only, so it works inside the MediaPipe virtual
environment. Copy this file next to gestures.py.

Use it from gestures.py, once per camera frame:

    from gesture_sender import send_gesture

    # gesture_name: e.g. "INDEX_RIGHT", or None when no hand / unknown
    # palm_xy: optional [x, y] of the palm in 0..1, or None
    send_gesture(gesture_name, palm_xy)

It sends one packet immediately when the gesture CHANGES, and repeats it every
KEEPALIVE_SEC while it stays the same. The repeats are essential: gesture_node
treats silence as "the recogniser died or the hand is gone".

Try it without a camera:   python3 gesture_sender.py
"""
import json
import socket
import sys
import time

HOST = "127.0.0.1"
PORT = 5005               # must match gesture_node's udp_port parameter
KEEPALIVE_SEC = 0.1       # 10 packets/s -> a new gesture is accepted in ~0.4 s

_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_last = {"gesture": "<unset>", "time": 0.0}


def send_gesture(gesture, xy=None):
    """Call every frame. Sends on change, and as a keep-alive while unchanged."""
    now = time.monotonic()
    changed = gesture != _last["gesture"]
    if not changed and now - _last["time"] < KEEPALIVE_SEC:
        return
    packet = {"gesture": gesture, "t": time.time(), "xy": xy}
    try:
        _sock.sendto(json.dumps(packet).encode("utf-8"), (HOST, PORT))
    except OSError:
        pass                  # nobody listening is not an error for the recogniser
    _last["gesture"] = gesture
    _last["time"] = now


def _demo():
    """Fake camera: cycles through gestures so you can watch gesture_node react."""
    script = [
        ("OPEN_HAND_UP", 2.0),
        ("INDEX_RIGHT", 3.0),
        (None, 0.6),            # short gap: node goes NEUTRAL
        ("CLOSED_HAND", 2.0),
        (None, 3.0),            # long gap: node asks HOVER_LAND_REQUEST
        ("INDEX_LEFT", 2.0),
    ]
    print(f"Demo: sending to {HOST}:{PORT}. Ctrl+C to stop.")
    for gesture, seconds in script:
        print(f"  sending {gesture} for {seconds} s")
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            send_gesture(gesture, [0.5, 0.5] if gesture else None)
            time.sleep(1 / 30)        # pretend 30 FPS
    print("Demo finished.")


if __name__ == "__main__":
    try:
        _demo()
    except KeyboardInterrupt:
        sys.exit(0)
