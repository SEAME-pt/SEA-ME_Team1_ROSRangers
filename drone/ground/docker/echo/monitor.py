"""Live table of the gesture -> intent -> mission chain, redrawn in place like `top`.

    docker compose up --build             # from drone/ground/: the whole stack, table here
    docker compose run --rm echo          # or the table alone, in its own terminal

Rows: the gesture gesture_node sees (/gesture/raw), the intent intent_node
sends (/gesture/intent), mission_node's mode (/mission/state) and its velocity
target (/mission/velocity_target, body frame FLU). A row is "live" while its
topic keeps arriving and turns "STALE" when it stops. The screen is only
redrawn when the text changes, so it does not flicker.

Without a terminal (piped, or a service without `tty: true`), it prints one
line each time something changes instead of redrawing. Ctrl+C or SIGTERM exits 0.
"""

import json
import math
import signal
import sys
import threading
import time

import rclpy
from geometry_msgs.msg import TwistStamped
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import String

REFRESH_SEC = 0.1
STALE_SEC = 1.0  # every topic here arrives at 2 Hz or faster

# ANSI: clear the screen and hide the cursor / show it again, cursor home,
# clear to end of line / screen. No alternate screen: `docker compose up` stops
# relaying output before LEAVE, which would leave the terminal stuck in it.
ENTER, LEAVE = "\033[2J\033[?25l", "\033[?25h"
HOME, EOL, EOS = "\033[H", "\033[K", "\033[J"
# Synchronized output: terminals that support it (VS Code, kitty, WezTerm,
# recent GNOME Terminal) show the whole frame at once; others ignore it.
SYNC_ON, SYNC_OFF = "\033[?2026h", "\033[?2026l"
# `docker compose up` writes "echo-1  | " before every line it relays. Each line
# starts with "\r" and is written over it, then EOL clears what is left: no
# blank line in between, so nothing flashes. Harmless elsewhere.


def _json(msg):
    try:
        data = json.loads(msg.data)
    except (ValueError, RecursionError):
        return {"bad": msg.data[:60]}
    return data if isinstance(data, dict) else {"bad": msg.data[:60]}


def _speed(value, plus, minus, unit):
    if abs(value) < 0.005:
        return "0"
    return f"{plus if value > 0 else minus} {abs(value):.2f} {unit}"


class Monitor:
    def __init__(self, node):
        self.rows = {}  # name -> (time received, {field: text})
        self.lock = threading.Lock()
        node.create_subscription(String, "/gesture/raw", self._raw, 10)
        node.create_subscription(String, "/gesture/intent", self._intent, 10)
        node.create_subscription(String, "/mission/state", self._state, 10)
        node.create_subscription(
            TwistStamped, "/mission/velocity_target", self._velocity, 10
        )

    def _set(self, name, fields):
        with self.lock:
            self.rows[name] = (time.monotonic(), fields)

    def _raw(self, msg):
        d = _json(msg)
        self._set("gesture", {"gesture": d.get("gesture"), "bad": d.get("bad")})

    def _intent(self, msg):
        d = _json(msg)
        self._set(
            "intent",
            {
                "intent": d.get("intent"),
                "reason": d.get("reason"),
                "gesture": d.get("gesture"),
                "bad": d.get("bad"),
            },
        )

    def _state(self, msg):
        d = _json(msg)
        self._set(
            "mission",
            {
                "mode": d.get("mode"),
                "why": d.get("mode_reason"),
                "mask": d.get("mask"),
                "airborne": d.get("airborne"),
                "limited": ", ".join(d.get("limited") or []) or None,
                "bad": d.get("bad"),
            },
        )

    def _velocity(self, msg):
        v, w = msg.twist.linear, msg.twist.angular
        self._set(
            "velocity",
            {
                "fwd/back": _speed(v.x, "forward", "back", "m/s"),
                "left/right": _speed(v.y, "left", "right", "m/s"),
                "up/down": _speed(v.z, "up", "down", "m/s"),
                "yaw": _speed(math.degrees(w.z), "left", "right", "deg/s"),
            },
        )

    def snapshot(self):
        """[(row name, age in s or None, {field: text})] in pipeline order."""
        now = time.monotonic()
        with self.lock:
            rows = dict(self.rows)
        out = []
        for name in ("gesture", "intent", "mission", "velocity"):
            if name in rows:
                t, fields = rows[name]
                out.append(
                    (name, now - t, {k: v for k, v in fields.items() if v is not None})
                )
            else:
                out.append((name, None, {}))
        return out


SOURCES = {
    "gesture": "gesture_node  /gesture/raw",
    "intent": "intent_node   /gesture/intent",
    "mission": "mission_node  /mission/state",
    "velocity": "mission_node  /mission/velocity_target",
}


def table(rows):
    lines = [
        f"Ground pipeline monitor   {time.strftime('%H:%M:%S')}   (Ctrl+C to quit)",
        "",
    ]
    for name, age, fields in rows:
        if age is None:
            status = "waiting"
        elif age > STALE_SEC:
            status = f"STALE {age:.0f} s"
        else:
            status = "live"  # not the age: it would change, and redraw, every frame
        lines.append(f"{name.upper():9} {SOURCES[name]:42} {status}")
        for key, value in fields.items():
            lines.append(f"    {key:11} {value}")
        lines.append("")
    return lines


def one_line(rows):
    parts = []
    for name, age, fields in rows:
        if age is None or age > STALE_SEC:
            parts.append(f"{name}: -")
        elif name == "velocity":
            parts.append(
                f"{name}: " + (" ".join(v for v in fields.values() if v != "0") or "0")
            )
        else:
            parts.append(f"{name}: " + " ".join(str(v) for v in fields.values()))
    return " | ".join(parts)


def main():
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stop.set())
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = rclpy.create_node("ground_monitor")
    monitor = Monitor(node)
    spinner = threading.Thread(target=lambda: _spin(node, stop), daemon=True)
    spinner.start()

    live = sys.stdout.isatty()
    last = None
    if live:
        sys.stdout.write(ENTER)
    try:
        while not stop.wait(REFRESH_SEC):
            rows = monitor.snapshot()
            if live:
                lines = table(rows)
                if lines == last:
                    continue
                last = lines
                # "\r" + EOS first, in the same relayed line as HOME: compose's
                # prefix for it lands below the last frame and is erased. Ends
                # with a newline: compose holds back a line until it is complete.
                sys.stdout.write(
                    SYNC_ON
                    + "\r"
                    + EOS
                    + HOME
                    + "".join("\r" + line + EOL + "\n" for line in lines)
                    + "\r"
                    + EOS
                    + SYNC_OFF
                    + "\n"
                )
                sys.stdout.flush()
            else:
                line = one_line(rows)
                if line != last:
                    print(line, flush=True)
                    last = line
    finally:
        if live:
            sys.stdout.write(LEAVE)
            sys.stdout.flush()
        spinner.join()
        node.destroy_node()
        rclpy.try_shutdown()


def _spin(node, stop):
    while not stop.is_set():
        rclpy.spin_once(node, timeout_sec=0.1)


if __name__ == "__main__":
    main()
