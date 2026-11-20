"""Live table of the gesture -> intent -> mission chain, redrawn in place like `top`.

    docker compose up --build             # from drone/ground/: the whole stack, table here
    docker compose run --rm echo          # or the table alone, in its own terminal

Rows: the drone's link and safety state (/flight/state, from flight_interface,
or the demo's / sim's stand-in), the drone's attitude (/fc/attitude, whole
degrees, the topic's REP-103 signs; only from flight_interface's ESP backend
in make real, or fc_telemetry on the bench), its battery (/fc/battery: pack
voltage and the average per cell; same sources), the
gesture gesture_node sees (/gesture/raw), the intent intent_node
sends (/gesture/intent), mission_node's mode (/mission/state) and its velocity
target (/mission/velocity_target, body frame FLU). A row is "live" while its
topic keeps arriving and turns "STALE" when it stops. The screen is only
redrawn when the text changes, so it does not flicker.

Alerts from /flight/state (E-stop, failsafe, lost link, FC link, the topic
stopping) and /fc/battery (LOW BATTERY: at or below 3.5 V/cell for 2 s) go
on a line under the title, marked "!!": red for a fault, yellow
for "not OK yet" (FC link unknown, node not activated). Colour only on a
terminal; the "!!" text is always there.

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
from geometry_msgs.msg import TwistStamped, Vector3Stamped
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

REFRESH_SEC = 0.1
STALE_SEC = 1.0  # every topic here arrives at 2 Hz or faster

# Battery: land at 3.5 V per cell (Safety Rules #8, Battery Log §1). The alert
# needs LOW_BATTERY_HOLD_SEC below it without a break, so the short sag under
# throttle does not flash it. Betaflight's own warnings and the radio stay
# authoritative; the table only adds information. It sees the pack voltage only,
# never single cells, so it does not replace the per-cell check (REQ-PWR-001).
LAND_PER_CELL_V = 3.5
LOW_BATTERY_HOLD_SEC = 2.0

# ANSI: clear the screen and hide the cursor / show it again, cursor home,
# clear to end of line / screen. No alternate screen: `docker compose up` stops
# relaying output before LEAVE, which would leave the terminal stuck in it.
ENTER, LEAVE = "\033[2J\033[?25l", "\033[?25h"
HOME, EOL, EOS = "\033[H", "\033[K", "\033[J"
# Synchronized output: terminals that support it (VS Code, kitty, WezTerm,
# recent GNOME Terminal) show the whole frame at once; others ignore it.
SYNC_ON, SYNC_OFF = "\033[?2026h", "\033[?2026l"
# Alert colours, on a terminal only. The "!!" marker carries the alert without
# colour (piped output, terminals without colour).
RED, YELLOW, RESET = "\033[31m", "\033[33m", "\033[0m"
# `docker compose up` writes "echo-1  | " before every line it relays. Each line
# starts with "\r" and is written over it, then EOL clears what is left: no
# blank line in between, so nothing flashes. Harmless elsewhere.


def _json(msg):
    try:
        data = json.loads(msg.data)
    except (ValueError, RecursionError):
        return {"bad": msg.data[:60]}
    return data if isinstance(data, dict) else {"bad": msg.data[:60]}


# /flight/state: shown whenever the publisher sends them. flight_interface
# sends all of them; the demo's fake_flight_state and the sim's bridge only
# link_ok, esp_state, override_active and mask. A null shows "unknown", never
# disappears: unknown counts as not OK (ADR-025 E6.1).
FLIGHT_SAFETY = (
    "active",
    "link_ok",
    "esp_state",
    "fc_link_ok",
    "armed",
    "override_active",
    "mask",
    "failsafe_output",
    "estop",
)
# Without a terminal, one line per change: only these, as key=value.
FLIGHT_ONE_LINE = ("link_ok", "esp_state", "armed", "estop")


def _flight_fields(d):
    """The FLIGHT row: the safety fields, then the extras only when they matter."""
    if "bad" in d:
        return {"bad": d["bad"]}
    fields = {}
    for key in FLIGHT_SAFETY:
        if key not in d:
            continue
        value = d[key]
        if value is None:
            fields[key] = "unknown"
        else:
            fields[key] = value
    if d.get("estop") is True:
        fields["estop_reason"] = d.get("estop_reason")
    for key in ("arm_pending", "resume_pending"):
        if d.get(key) is True:
            fields[key] = True
    rejected = d.get("esp_rx_rejected")
    if isinstance(rejected, int) and not isinstance(rejected, bool) and rejected > 0:
        fields["esp_rx_rejected"] = rejected
    return fields


def _speed(value, plus, minus, unit):
    if abs(value) < 0.005:
        return "0"
    return f"{plus if value > 0 else minus} {abs(value):.2f} {unit}"


def _degrees(rad):
    """An /fc/attitude angle as whole degrees with its sign: "+3 deg", "-10 deg",
    "0 deg". The sign is the topic's own (REP-103): roll + right side down,
    pitch + nose down, yaw + counter-clockwise (left).

    Halves round away from zero, e.g. 2.5 -> +3 and -2.5 -> -3 (Python's
    round() would give 2 and -2). Worked example: 0.1 rad = 5.73 deg -> "+6 deg".
    """
    deg = math.degrees(rad)
    if deg >= 0:
        whole = math.floor(deg + 0.5)
    else:
        whole = -math.floor(-deg + 0.5)
    if whole == 0:
        return "0 deg"  # also for -0.4: no "+0" / "-0"
    return f"{whole:+d} deg"


class Monitor:
    def __init__(self, node):
        self.rows = {}  # name -> (time received, {field: text})
        self.lock = threading.Lock()
        self.low_since = None  # when the battery went to or below the land level
        node.create_subscription(String, "/flight/state", self._flight, 10)
        node.create_subscription(Vector3Stamped, "/fc/attitude", self._attitude, 10)
        node.create_subscription(BatteryState, "/fc/battery", self._battery, 10)
        node.create_subscription(String, "/gesture/raw", self._raw, 10)
        node.create_subscription(String, "/gesture/intent", self._intent, 10)
        node.create_subscription(String, "/mission/state", self._state, 10)
        node.create_subscription(
            TwistStamped, "/mission/velocity_target", self._velocity, 10
        )

    def _set(self, name, fields):
        with self.lock:
            self.rows[name] = (time.monotonic(), fields)

    def _flight(self, msg):
        self._set("flight", _flight_fields(_json(msg)))

    def _attitude(self, msg):
        # One line: x roll, y pitch, z yaw (rad). Yaw is relative: zero at
        # flight_interface's activate (or fc_telemetry's start), no compass.
        v = msg.vector
        angles = (
            f"roll: {_degrees(v.x)}    "
            f"pitch: {_degrees(v.y)}    "
            f"yaw: {_degrees(v.z)} (from start)"
        )
        self._set("attitude", {"angles": angles})

    def _battery(self, msg):
        # Only msg.voltage is measured; cell_voltage holds one NaN per cell
        # (telemetry.py), so the cell count is its length and the per-cell
        # voltage the average: 14.80 V / 4 cells = 3.70 V/cell.
        voltage = msg.voltage
        cells = len(msg.cell_voltage)
        per_cell = None
        if not msg.present or math.isnan(voltage):
            line = "no battery (USB power)"
        elif cells == 0:
            line = f"{voltage:.2f} V"
        else:
            per_cell = voltage / cells
            line = f"{voltage:.2f} V    {per_cell:.2f} V/cell ({cells} cells)"
        now = time.monotonic()
        with self.lock:
            low = per_cell is not None and per_cell <= LAND_PER_CELL_V
            if not low:
                self.low_since = None
            elif self.low_since is None:
                self.low_since = now
            self.rows["battery"] = (now, {"line": line, "per_cell": per_cell})

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
        # No mask: mission_node copies it from /flight/state (the FLIGHT row).
        # The reason only in IDLE, where it says why gestures do nothing; in the
        # other modes it only repeats the mode (engage, lock, unlock, override_on).
        d = _json(msg)
        mode = d.get("mode")
        if mode == "IDLE":
            why = d.get("mode_reason")
        else:
            why = None
        self._set(
            "mission",
            {
                "mode": mode,
                "why": why,
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
            low_since = self.low_since
        out = []
        for name in ROW_ORDER:
            if name in rows:
                t, fields = rows[name]
                if name == "battery" and low_since is not None:
                    # How long it has been at or below the land level (not shown).
                    fields = dict(fields, low_for=now - low_since)
                out.append(
                    (name, now - t, {k: v for k, v in fields.items() if v is not None})
                )
            else:
                out.append((name, None, {}))
        return out


# The drone first: it is what the pilot and the audience look at first.
ROW_ORDER = (
    "flight",
    "attitude",
    "battery",
    "gesture",
    "intent",
    "mission",
    "velocity",
)
# The drone's rows that print one line of text, not "key  value" lines.
ONE_TEXT_LINE = {"attitude": "angles", "battery": "line"}

SOURCES = {
    "flight": "/flight/state",  # the publisher depends on the mode
    "attitude": "/fc/attitude",
    "battery": "/fc/battery",
    "gesture": "gesture_node  /gesture/raw",
    "intent": "intent_node   /gesture/intent",
    "mission": "mission_node  /mission/state",
    "velocity": "mission_node  /mission/velocity_target",
}


def alerts(rows):
    """[(colour, text, flagged "row.field" names)], most severe first: the
    FLIGHT row's red alerts, then LOW BATTERY, then FLIGHT's yellow ones. RED
    is a fault; YELLOW is not OK yet, but expected (unknown FC link at the start
    of a session, node not activated yet)."""
    by_name = {name: (age, fields) for name, age, fields in rows}
    red, yellow = _flight_alerts(*by_name["flight"])
    red = red + _battery_alerts(*by_name["battery"])
    return red + yellow


def _battery_alerts(age, fields):
    """LOW BATTERY once the pack has been at or below LAND_PER_CELL_V for
    LOW_BATTERY_HOLD_SEC. None from a STALE row: the old value is no longer
    the drone's (FC LINK LOST on the FLIGHT row says why)."""
    if age is None or age > STALE_SEC:
        return []
    low_for = fields.get("low_for")
    if low_for is None or low_for < LOW_BATTERY_HOLD_SEC:
        return []
    text = f"LOW BATTERY: {fields['per_cell']:.2f} V/cell"
    return [(RED, text, ("battery.line",))]


def _flight_alerts(age, fields):
    """(red, yellow) from the FLIGHT row.

    Nothing before the first /flight/state ("waiting" is the normal start of
    make real). Once it stops (STALE), only that alert: the old values on the
    row are no longer the drone's state.
    """
    if age is None:
        return [], []
    if age > STALE_SEC:
        return [(RED, f"NO /flight/state ({age:.0f} s)", ())], []
    if "bad" in fields:
        return [(RED, "BAD /flight/state", ("flight.bad",))], []

    red = []
    yellow = []
    if fields.get("estop") is True:
        reason = fields.get("estop_reason")
        if reason:
            red.append(
                (RED, f"E-STOP: {reason}", ("flight.estop", "flight.estop_reason"))
            )
        else:
            red.append((RED, "E-STOP", ("flight.estop",)))
    failsafe = fields.get("failsafe_output") is True
    if failsafe or fields.get("esp_state") == "LINK_FAILSAFE":
        red.append((RED, "FAILSAFE", ("flight.failsafe_output", "flight.esp_state")))
    if fields.get("fc_link_ok") is False:
        red.append((RED, "FC LINK LOST", ("flight.fc_link_ok",)))
    if fields.get("fc_link_ok") == "unknown":
        yellow.append((YELLOW, "FC LINK UNKNOWN", ("flight.fc_link_ok",)))
    if fields.get("active") is False:
        yellow.append((YELLOW, "NOT ACTIVE", ("flight.active",)))
    # link_ok is false whenever one of the above is true (guard.py: active, no
    # E-stop, FC link alive, fresh link). NO LINK only when none explains it.
    link_ok = fields.get("link_ok", True)  # a stand-in may not send it
    if link_ok is not True and not red and not yellow:
        red.append((RED, "NO LINK", ("flight.link_ok",)))
    # Severity order: E-STOP, FAILSAFE, FC LINK LOST / NO LINK.
    return red, yellow


def _paint(text, colour, on):
    if on:
        return colour + text + RESET
    return text


def table(rows, colour=False):
    """The frame's lines. colour=True only on a terminal (main())."""
    found = alerts(rows)
    lines = [
        f"Ground pipeline monitor   {time.strftime('%H:%M:%S')}   (Ctrl+C to quit)",
    ]
    if found:
        marks = [_paint(f"!! {text}", c, colour) for c, text, _ in found]
        lines.append("  ".join(marks))
    lines.append("")
    flagged = {}  # "row.field" -> colour; the most severe alert comes first
    for c, _, keys in found:
        for key in keys:
            flagged.setdefault(key, c)
    for name, age, fields in rows:
        if age is None:
            status = "waiting"
        elif age > STALE_SEC:
            status = f"STALE {age:.0f} s"
        else:
            status = "live"  # not the age: it would change, and redraw, every frame
        lines.append(f"{name.upper():9} {SOURCES[name]:42} {status}")
        for key, value in fields.items():
            if name in ONE_TEXT_LINE:
                if key != ONE_TEXT_LINE[name]:
                    continue  # per_cell, low_for: for the alerts, not shown
                line = f"    {value}"
            else:
                line = f"    {key:11} {value}"
            flag = f"{name}.{key}"
            if flag in flagged:
                line = _paint(line, flagged[flag], colour)
            lines.append(line)
        lines.append("")
    return lines


def one_line(rows):
    parts = []
    found = alerts(rows)
    if found:
        parts.append(" ".join(f"!! {text}" for _, text, _ in found))
    for name, age, fields in rows:
        if name in ("attitude", "battery"):
            # Change on almost every message (angles, the voltage's last digit):
            # up to 10 lines a second. LOW BATTERY still shows in the "!!" part.
            # Record a rosbag to analyse them instead.
            continue
        if age is None or age > STALE_SEC:
            parts.append(f"{name}: -")
        elif name == "flight" and "bad" not in fields:
            pairs = [f"{k}={fields[k]}" for k in FLIGHT_ONE_LINE if k in fields]
            parts.append(f"{name}: " + " ".join(pairs))
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
                lines = table(rows, colour=True)
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
