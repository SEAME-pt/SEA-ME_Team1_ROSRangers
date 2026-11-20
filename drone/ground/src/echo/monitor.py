"""Live table of the ground and the drone, redrawn in place like `top`.

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
stopping), /fc/battery (LOW BATTERY: at or below 3.5 V/cell for 2 s) and two
publishers on a one-publisher topic (/flight/state, /mission/velocity_target,
/fc/attitude, /fc/battery; for 1 s) go on a line under the title, marked
"!!": red for a fault, yellow for "not OK yet" (FC link unknown, node not
activated). Colour only on a terminal; the "!!" text is always there.

The title line is the mode's banner (GROUND_MODE from env/<mode>.env, plus
GROUND_DRY=1 from make real-dry): DEMO, REAL, REAL-DRY or SIM, so a demo is
never taken for the drone. A row that never got a message says why under its
heading (e.g. no /fc/* in demo or sim).

Without a terminal (piped, or a service without `tty: true`), it prints one
line each time something changes instead of redrawing (after one first
"mode: ..." line). Ctrl+C or SIGTERM exits 0.
"""

import json
import math
import os
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


# Topics where exactly one publisher is the rule. A second one is never part of
# a mode: /flight/state (mission_node mixes two drone states), the velocity
# target (commands that skip the gestures reach the drone), /fc/* (fc_telemetry
# next to flight_interface, which link_node.py forbids). The Makefile guard
# (ADR-025 E3.2) stops the usual way in; this shows one that got around it, e.g.
# a host `ros2 launch` on the same domain. Not the gesture topics: a second
# publisher there is a documented test (ros2 topic pub /gesture/raw,
# fake_intent, make sim-smoke). Add /control/attitude_setpoint with #199.
SINGLE_PUBLISHER_TOPICS = (
    "/flight/state",
    "/mission/velocity_target",
    "/fc/attitude",
    "/fc/battery",
)
PUBLISHERS_CHECK_SEC = 0.5  # graph query period
PUBLISHERS_HOLD_SEC = 1.0  # more than one, without a break, before the alert


class Monitor:
    def __init__(self, node):
        self.node = node
        self.rows = {}  # name -> (time received, {field: text})
        self.lock = threading.Lock()
        self.low_since = None  # when the battery went to or below the land level
        # Topics with more than one publisher: topic -> (since, [node names]).
        self.extra = {}
        # Reads the ROS graph only (who publishes): no client, no publisher.
        node.create_timer(PUBLISHERS_CHECK_SEC, self._check_publishers)
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

    def _check_publishers(self):
        """Runs in the spin thread, every PUBLISHERS_CHECK_SEC."""
        now = time.monotonic()
        for topic in SINGLE_PUBLISHER_TOPICS:
            names = []
            for info in self.node.get_publishers_info_by_topic(topic):
                namespace = info.node_namespace
                if namespace in ("", "/"):
                    names.append(info.node_name)
                else:
                    names.append(f"{namespace}/{info.node_name}")
            with self.lock:
                if len(names) <= 1:
                    self.extra.pop(topic, None)  # a break restarts the hold
                elif topic in self.extra:
                    since = self.extra[topic][0]
                    self.extra[topic] = (since, sorted(names))
                else:
                    self.extra[topic] = (now, sorted(names))

    def doubled(self):
        """[(topic, [node names])] for each topic that has had more than one
        publisher for PUBLISHERS_HOLD_SEC, in SINGLE_PUBLISHER_TOPICS order."""
        now = time.monotonic()
        with self.lock:
            extra = dict(self.extra)
        out = []
        for topic in SINGLE_PUBLISHER_TOPICS:
            if topic not in extra:
                continue
            since, names = extra[topic]
            if now - since >= PUBLISHERS_HOLD_SEC:
                out.append((topic, names))
        return out

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

# ---------- The ground mode (env/<mode>.env, ADR-025 E1.3) ----------
# GROUND_MODE comes from the mode's env file through docker-compose.yml (x-ros).
# make real-dry uses env/real.env too, so it also passes GROUND_DRY=1 (Makefile):
# without it a dry run would look exactly like the real drone.
BANNERS = {
    "demo": "DEMO: PRETEND /flight/state",
    "real": "REAL: DRONE",
    "real-dry": "REAL-DRY: NO DRONE (loopback)",
    "sim": "SIM: PX4 SITL",
    None: "MODE ?",  # GROUND_MODE empty or unknown, e.g. monitor.py on the host
}
# Who publishes /flight/state in each mode. sim changes with #199 (flight_interface
# with the PX4 backend replaces the bridge).
FLIGHT_SOURCES = {
    "demo": "fake_flight_state  /flight/state",
    "real": "flight_interface  /flight/state",
    "real-dry": "flight_interface  /flight/state",
    "sim": "px4_offboard_bridge  /flight/state",
}
# Why ATTITUDE and BATTERY have had no message yet, per mode.
NO_FC = {
    "demo": "no data: demo has no drone",
    "sim": "no data: the sim publishes no /fc/*",
    "real-dry": "no data: loopback has no FC",
    "real": "waiting: configure flight_interface; needs fresh FC data",
}
NO_FLIGHT_STATE = "waiting: configure flight_interface (make real-steps)"


def waiting_reason(mode, name):
    """The line under a row that never got a message, or None."""
    if name in ("attitude", "battery"):
        return NO_FC.get(mode)
    if name == "flight" and mode in ("real", "real-dry"):
        return NO_FLIGHT_STATE  # flight_interface publishes from configure on
    return None


def ground_mode(environ):
    """ "demo", "real", "real-dry", "sim", or None when GROUND_MODE is empty or
    unknown."""
    mode = environ.get("GROUND_MODE", "")
    if mode not in ("demo", "real", "sim"):
        return None
    if mode == "real" and environ.get("GROUND_DRY") == "1":
        return "real-dry"
    return mode


def alerts(rows, doubled=()):
    """[(colour, text, flagged "row.field" names)], most severe first: two
    publishers on one topic (Monitor.doubled()), the FLIGHT row's red alerts,
    LOW BATTERY, then FLIGHT's yellow ones. RED is a fault; YELLOW is not OK
    yet, but expected (unknown FC link at the start of a session, node not
    activated yet)."""
    by_name = {name: (age, fields) for name, age, fields in rows}
    red = []
    for topic, names in doubled:
        text = f"{len(names)} {topic} PUBLISHERS: {', '.join(names)}"
        red.append((RED, text, ()))
    flight_red, yellow = _flight_alerts(*by_name["flight"])
    red = red + flight_red + _battery_alerts(*by_name["battery"])
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
    # esp_state null: no fresh state from the drone (guard.py blanks every
    # drone field when the ESP goes silent), so the link to the ESP is lost.
    # The FC link is unknown then only because of that: NO LINK, red, also
    # next to the yellow alerts. The demo's and the sim's stand-ins always
    # send an esp_state.
    drone_silent = fields.get("esp_state") == "unknown"
    if drone_silent:
        red.append((RED, "NO LINK", ("flight.link_ok", "flight.esp_state")))
    elif fields.get("fc_link_ok") == "unknown":
        yellow.append((YELLOW, "FC LINK UNKNOWN", ("flight.fc_link_ok",)))
    if fields.get("active") is False:
        yellow.append((YELLOW, "NOT ACTIVE", ("flight.active",)))
    # link_ok is false whenever one of the above is true (guard.py: active, no
    # E-stop, FC link alive, fresh link). Otherwise NO LINK only when none
    # explains it (a silent drone added it above already).
    link_ok = fields.get("link_ok", True)  # a stand-in may not send it
    if link_ok is not True and not red and not yellow:
        red.append((RED, "NO LINK", ("flight.link_ok",)))
    # Severity order: E-STOP, FAILSAFE, FC LINK LOST / NO LINK.
    return red, yellow


def _paint(text, colour, on):
    if on:
        return colour + text + RESET
    return text


# Axes the ground owns per override mask: a copy of mission_logic.MASK_AXES
# ("04 Mission Node" §5.3; the echo image has no mission_node to import it
# from). test_monitor.py checks the two stay equal. Any other mask: mission_node
# stays IDLE (why: bad_mask).
MASK_AXES = {
    8: {"yaw"},
    11: {"x", "y", "yaw"},
    15: {"x", "y", "z", "yaw"},
}
# The velocity row's words for each axis, in its order.
AXIS_WORDS = (
    ("x", "fwd/back"),
    ("y", "left/right"),
    ("z", "up/down"),
    ("yaw", "yaw"),
)


def mask_note(rows):
    """The VELOCITY row's last line: which axes the ground owns, from
    /flight/state's mask, so "the gesture does nothing" reads as "mask 8: yaw
    only". None when FLIGHT has no usable mask (waiting, STALE, bad, missing or
    unknown): the FLIGHT row and its alerts already say why."""
    _, age, fields = next(row for row in rows if row[0] == "flight")
    if age is None or age > STALE_SEC or "bad" in fields:
        return None
    mask = fields.get("mask")
    if not isinstance(mask, int) or isinstance(mask, bool):
        return None  # missing, "unknown" or not a number
    if mask not in MASK_AXES:
        return f"mask {mask}: not a ground mask, mission stays IDLE"
    owned = MASK_AXES[mask]
    words = []
    for axis, word in AXIS_WORDS:
        if axis in owned:
            words.append(word)
    if len(words) == len(AXIS_WORDS):
        text = "all"
    elif words == ["yaw"]:
        text = "yaw only"
    else:
        text = ", ".join(words)
    return f"ground owns: {text} (mask {mask})"


def table(rows, colour=False, mode=None, doubled=()):
    """The frame's lines. colour=True only on a terminal (main()). mode is
    ground_mode()'s value: it picks the banner, the FLIGHT row's publisher and
    the reasons for rows that never got a message. doubled: Monitor.doubled()."""
    found = alerts(rows, doubled)
    banner = BANNERS[mode]
    lines = [
        f"{banner}   {time.strftime('%H:%M:%S')}   (Ctrl+C to quit)",
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
        source = SOURCES[name]
        if name == "flight" and mode in FLIGHT_SOURCES:
            source = FLIGHT_SOURCES[mode]
        lines.append(f"{name.upper():9} {source:42} {status}")
        reason = waiting_reason(mode, name)
        if age is None and reason:
            lines.append(f"    {reason}")
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
        if name == "velocity":
            note = mask_note(rows)
            if note:
                lines.append(f"    {note}")
        lines.append("")
    return lines


def one_line(rows, doubled=()):
    parts = []
    found = alerts(rows, doubled)
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

    mode = ground_mode(os.environ)  # read once: the container's mode is fixed
    live = sys.stdout.isatty()
    last = None
    if live:
        sys.stdout.write(ENTER)
    else:
        print(f"mode: {BANNERS[mode]}", flush=True)  # once, not on every line
    try:
        while not stop.wait(REFRESH_SEC):
            rows = monitor.snapshot()
            doubled = monitor.doubled()
            if live:
                lines = table(rows, colour=True, mode=mode, doubled=doubled)
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
                line = one_line(rows, doubled)
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
