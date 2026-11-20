"""The echo container's live table: drone/ground/src/echo/monitor.py.

`docker compose up` shows this table, so a broken monitor hides the whole
pipeline. Checks:
  * what each row shows for the messages of gesture_node, intent_node and
    mission_node, and that bad JSON never crashes it;
  * the table text only changes when a message changes it (no millisecond
    ages): a redraw is only sent then, so the terminal does not flicker;
  * as a program without a terminal: one line per change, no escape codes;
  * as a program on a terminal (a pty): frames wrapped in synchronized output,
    each ending with a newline (`docker compose up` only relays complete
    lines), no redraws while nothing changes, and the cursor comes back on
    Ctrl+C / SIGTERM, exit 0.

Needs ROS 2 Jazzy sourced (monitor.py imports rclpy). Without rclpy the tests
are skipped, unless REQUIRE_ROS_TESTS=1 is set (CI sets it), then it fails.

Run with:  python3 -m pytest drone/tests/ros -v        (from the repo root, ROS sourced)
"""

import json
import math
import os
import pty
import select
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from support import ros_available

MONITOR = Path(__file__).resolve().parents[2] / "ground" / "src" / "echo" / "monitor.py"

if ros_available():
    import rclpy
    from geometry_msgs.msg import TwistStamped, Vector3Stamped
    from rclpy.executors import SingleThreadedExecutor
    from sensor_msgs.msg import BatteryState
    from std_msgs.msg import String

    sys.path.insert(0, str(MONITOR.parent))
    import monitor
else:
    rclpy = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 91

RAW = {"gesture": "INDEX_RIGHT", "t": 1.0, "xy": None}
INTENT = {
    "intent": "MOVE_RIGHT",
    "gesture": "INDEX_RIGHT",
    "reason": "ok",
    "t": 1.0,
    "xy": None,
}
STATE = {
    "mode": "ENGAGED",
    "mode_reason": "engage",
    "intent": "MOVE_RIGHT",
    "reason": "ok",
    "gesture": "INDEX_RIGHT",
    "mask": 11,
    "airborne": True,
    "land_request": False,
    "limited": ["ramping:y"],
    "intent_age_ms": 12,
}
# flight_interface's /flight/state (guard.py flight_state()), healthy and streaming.
FLIGHT = {
    "link_ok": True,
    "esp_state": "STREAMING",
    "override_active": True,
    "mask": 8,
    "airborne": True,
    "armed": True,
    "failsafe_output": False,
    "fc_link_ok": True,
    "esp_rx_rejected": 0,
    "active": True,
    "estop": False,
    "estop_reason": None,
    "resume_pending": False,
    "arm_pending": False,
}
# The demo's fake_flight_state and the sim's bridge send only these.
FLIGHT_STANDIN = {
    "link_ok": True,
    "esp_state": "STREAMING",
    "override_active": True,
    "mask": 11,
    "airborne": True,
}


class FakeNode:
    """Stands in for the rclpy node: keeps the callback of each topic."""

    def __init__(self):
        self.callbacks = {}

    def create_subscription(self, msg_type, topic, callback, depth):
        self.callbacks[topic] = callback


def string(data):
    return String(data=data if isinstance(data, str) else json.dumps(data))


def twist(vx=0.0, vy=0.0, vz=0.0, wz=0.0):
    msg = TwistStamped()
    msg.twist.linear.x, msg.twist.linear.y, msg.twist.linear.z = vx, vy, vz
    msg.twist.angular.z = wz
    return msg


def attitude(roll_deg=0.0, pitch_deg=0.0, yaw_deg=0.0):
    """An /fc/attitude message from degrees (the topic is in radians)."""
    msg = Vector3Stamped()
    msg.vector.x = math.radians(roll_deg)
    msg.vector.y = math.radians(pitch_deg)
    msg.vector.z = math.radians(yaw_deg)
    return msg


def battery(voltage, cells=4, present=True):
    """An /fc/battery message as flight_interface sends it: only the voltage
    is measured, cell_voltage holds one NaN per cell (telemetry.py)."""
    msg = BatteryState()
    msg.voltage = voltage
    msg.present = present
    msg.cell_voltage = [float("nan")] * cells
    return msg


class MonitorTestCase(unittest.TestCase):
    """A Monitor on a FakeNode: messages go straight to its callbacks."""

    def setUp(self):
        self.node = FakeNode()
        self.monitor = monitor.Monitor(self.node)

    def feed(self, topic, msg):
        self.node.callbacks[topic](msg)

    def rows(self):
        return {name: fields for name, _, fields in self.monitor.snapshot()}


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class RowTests(MonitorTestCase):
    def test_subscribes_to_the_whole_chain(self):
        self.assertEqual(
            set(self.node.callbacks),
            {
                "/flight/state",
                "/fc/attitude",
                "/fc/battery",
                "/gesture/raw",
                "/gesture/intent",
                "/mission/state",
                "/mission/velocity_target",
            },
        )

    def test_rows_are_in_pipeline_order_and_wait_for_a_first_message(self):
        snapshot = self.monitor.snapshot()
        self.assertEqual(
            [name for name, _, _ in snapshot],
            [
                "flight",
                "attitude",
                "battery",
                "gesture",
                "intent",
                "mission",
                "velocity",
            ],
        )
        self.assertEqual([age for _, age, _ in snapshot], [None] * 7)

    def test_flight_row_from_flight_interface(self):
        self.feed("/flight/state", string(FLIGHT))
        rows = self.rows()
        # Safety fields in a fixed order; the extras (estop_reason, the pending
        # flags, esp_rx_rejected at 0) stay hidden while they do not matter.
        self.assertEqual(
            list(rows["flight"].items()),
            [
                ("active", True),
                ("link_ok", True),
                ("esp_state", "STREAMING"),
                ("fc_link_ok", True),
                ("armed", True),
                ("override_active", True),
                ("mask", 8),
                ("failsafe_output", False),
                ("estop", False),
            ],
        )

    def test_flight_row_from_the_demo_or_sim_stand_in(self):
        self.feed("/flight/state", string(FLIGHT_STANDIN))
        self.assertEqual(
            self.rows()["flight"],
            {
                "link_ok": True,
                "esp_state": "STREAMING",
                "override_active": True,
                "mask": 11,
            },
        )

    def test_flight_unknown_is_shown_not_dropped(self):
        """ADR-025 E6.1: unknown counts as not OK, so it must stay visible."""
        self.feed(
            "/flight/state",
            string(
                dict(FLIGHT, armed=None, fc_link_ok=None, esp_state=None, mask=None)
            ),
        )
        flight = self.rows()["flight"]
        for key in ("armed", "fc_link_ok", "esp_state", "mask"):
            with self.subTest(key=key):
                self.assertEqual(flight[key], "unknown")

    def test_flight_extras_only_when_they_matter(self):
        self.feed(
            "/flight/state",
            string(
                dict(
                    FLIGHT,
                    estop=True,
                    estop_reason="service",
                    arm_pending=True,
                    resume_pending=True,
                    esp_rx_rejected=3,
                )
            ),
        )
        flight = self.rows()["flight"]
        self.assertEqual(flight["estop_reason"], "service")
        self.assertIs(flight["arm_pending"], True)
        self.assertIs(flight["resume_pending"], True)
        self.assertEqual(flight["esp_rx_rejected"], 3)
        # An E-stop with no reason given: no empty field.
        self.feed("/flight/state", string(dict(FLIGHT, estop=True)))
        self.assertNotIn("estop_reason", self.rows()["flight"])
        # null or not a number: hidden, like 0.
        for value in (None, True, "3"):
            with self.subTest(esp_rx_rejected=value):
                self.feed("/flight/state", string(dict(FLIGHT, esp_rx_rejected=value)))
                self.assertNotIn("esp_rx_rejected", self.rows()["flight"])

    def test_flight_bad_message_is_shown_not_raised(self):
        for text in ("{not json", "[1, 2]", "null"):
            with self.subTest(text=text):
                self.feed("/flight/state", String(data=text))
                self.assertEqual(self.rows()["flight"], {"bad": text[:60]})

    def test_each_row_shows_its_topic(self):
        self.feed("/gesture/raw", string(RAW))
        self.feed("/gesture/intent", string(INTENT))
        self.feed("/mission/state", string(STATE))
        rows = self.rows()
        self.assertEqual(rows["gesture"], {"gesture": "INDEX_RIGHT"})
        self.assertEqual(
            rows["intent"],
            {"intent": "MOVE_RIGHT", "reason": "ok", "gesture": "INDEX_RIGHT"},
        )
        self.assertEqual(
            rows["mission"],
            {
                "mode": "ENGAGED",
                "airborne": True,
                "limited": "ramping:y",
            },
        )

    def test_mission_why_only_in_idle_and_no_mask(self):
        """The mask is on the FLIGHT row (mission_node copies it); the reason
        only says something new in IDLE."""
        for mode, reason in (
            ("WAITING", "override_on"),
            ("ENGAGED", "unlock"),
            ("LOCKED", "lock"),
        ):
            with self.subTest(mode=mode):
                self.feed(
                    "/mission/state", string(dict(STATE, mode=mode, mode_reason=reason))
                )
                self.assertNotIn("why", self.rows()["mission"])
                self.assertNotIn("mask", self.rows()["mission"])
        self.feed(
            "/mission/state",
            string(
                dict(
                    STATE,
                    mode="IDLE",
                    mode_reason="override_off",
                    mask=None,
                    airborne=False,
                    limited=[],
                )
            ),
        )
        self.assertEqual(
            self.rows()["mission"],
            {"mode": "IDLE", "why": "override_off", "airborne": False},
        )

    def test_no_hand_is_shown_without_a_gesture(self):
        self.feed("/gesture/raw", string({"gesture": None, "t": 1.0, "xy": None}))
        self.assertEqual(self.rows()["gesture"], {})

    def test_velocity_in_words_body_frame_flu(self):
        self.feed("/mission/velocity_target", twist(vy=-0.3, wz=math.radians(20)))
        self.assertEqual(
            self.rows()["velocity"],
            {
                "fwd/back": "0",
                "left/right": "right 0.30 m/s",  # y is LEFT in FLU
                "up/down": "0",
                "yaw": "left 20.00 deg/s",  # + is counter-clockwise
            },
        )
        self.feed("/mission/velocity_target", twist(vx=0.1, vz=-0.2, wz=-0.1))
        self.assertEqual(
            self.rows()["velocity"],
            {
                "fwd/back": "forward 0.10 m/s",
                "left/right": "0",
                "up/down": "down 0.20 m/s",
                "yaw": f"right {math.degrees(0.1):.2f} deg/s",
            },
        )

    def test_tiny_speeds_are_zero(self):
        self.feed("/mission/velocity_target", twist(vx=0.004, vy=-0.004))
        self.assertEqual(self.rows()["velocity"]["fwd/back"], "0")
        self.assertEqual(self.rows()["velocity"]["left/right"], "0")

    def test_bad_messages_are_shown_not_raised(self):
        for text in ("{not json", "[1, 2]", '"MOVE_RIGHT"', "null", "[" * 100000):
            with self.subTest(text=text[:20]):
                self.feed("/gesture/intent", String(data=text))
                self.assertEqual(self.rows()["intent"], {"bad": text[:60]})


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class TableTests(MonitorTestCase):
    def table(self):
        with mock.patch.object(monitor.time, "strftime", return_value="12:00:00"):
            return monitor.table(self.monitor.snapshot())

    def status(self, name):
        line = next(line for line in self.table() if line.startswith(name.upper()))
        return line.split(monitor.SOURCES[name])[1].strip()

    def test_waiting_live_then_stale(self):
        self.assertEqual(self.status("intent"), "waiting")
        self.feed("/gesture/intent", string(INTENT))
        self.assertEqual(self.status("intent"), "live")
        when, fields = self.monitor.rows["intent"]
        self.monitor.rows["intent"] = (when - 5.0, fields)
        self.assertEqual(self.status("intent"), "STALE 5 s")

    def test_text_only_changes_when_a_message_changes_it(self):
        """main() redraws only when the text changes: a counter that moves on
        every frame (like "12 ms ago") would redraw, and flicker, 10 times a s."""
        self.feed("/gesture/intent", string(INTENT))
        self.feed("/mission/velocity_target", twist(vy=-0.3))
        first = self.table()
        time.sleep(0.15)
        self.feed("/gesture/intent", string(INTENT))  # same content again
        self.feed("/mission/velocity_target", twist(vy=-0.3))
        self.assertEqual(self.table(), first)
        self.feed("/gesture/intent", string(dict(INTENT, intent="MOVE_LEFT")))
        self.assertNotEqual(self.table(), first)

    def test_one_line_without_a_terminal(self):
        self.feed("/gesture/intent", string(INTENT))
        self.feed("/mission/velocity_target", twist(vy=-0.3))
        line = monitor.one_line(self.monitor.snapshot())
        self.assertEqual(
            line,
            "flight: - | gesture: - | intent: MOVE_RIGHT ok INDEX_RIGHT "
            "| mission: - | velocity: right 0.30 m/s",
        )
        self.feed("/mission/velocity_target", twist())
        self.assertIn("velocity: 0", monitor.one_line(self.monitor.snapshot()))

    def test_one_line_flight_has_only_the_key_values(self):
        self.feed("/flight/state", string(dict(FLIGHT, armed=None)))
        line = monitor.one_line(self.monitor.snapshot())
        self.assertTrue(
            line.startswith(
                "flight: link_ok=True esp_state=STREAMING armed=unknown estop=False | "
            ),
            line,
        )
        # The stand-in has no armed / estop: only what it sends.
        self.feed("/flight/state", string(FLIGHT_STANDIN))
        line = monitor.one_line(self.monitor.snapshot())
        self.assertTrue(
            line.startswith("flight: link_ok=True esp_state=STREAMING | "), line
        )
        self.feed("/flight/state", String(data="{not json"))
        self.assertTrue(
            monitor.one_line(self.monitor.snapshot()).startswith(
                "!! BAD /flight/state | flight: {not json |"
            )
        )


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class AlertTests(MonitorTestCase):
    """Phase 2 of #227: E-stop, failsafe and lost link stand out."""

    def alerts(self):
        return [(c, text) for c, text, _ in monitor.alerts(self.monitor.snapshot())]

    def flight(self, **changes):
        self.feed("/flight/state", string(dict(FLIGHT, **changes)))

    def age_flight(self, seconds):
        when, fields = self.monitor.rows["flight"]
        self.monitor.rows["flight"] = (when - seconds, fields)

    def test_none_while_healthy(self):
        self.flight()
        self.assertEqual(self.alerts(), [])
        self.feed("/flight/state", string(FLIGHT_STANDIN))
        self.assertEqual(self.alerts(), [])

    def test_none_before_the_first_flight_state(self):
        """'waiting' is the normal start of make real (before configure)."""
        self.assertEqual(self.alerts(), [])

    def test_estop_with_and_without_reason(self):
        self.flight(estop=True, estop_reason="service", link_ok=False)
        self.assertEqual(self.alerts(), [(monitor.RED, "E-STOP: service")])
        self.flight(estop=True, link_ok=False)
        self.assertEqual(self.alerts(), [(monitor.RED, "E-STOP")])

    def test_failsafe_from_the_flag_or_the_esp_state(self):
        self.flight(failsafe_output=True, link_ok=False)
        self.assertEqual(self.alerts(), [(monitor.RED, "FAILSAFE")])
        # The sim's bridge has no failsafe_output, only esp_state.
        self.feed(
            "/flight/state",
            string(dict(FLIGHT_STANDIN, link_ok=False, esp_state="LINK_FAILSAFE")),
        )
        self.assertEqual(self.alerts(), [(monitor.RED, "FAILSAFE")])

    def test_fc_link_lost_red_unknown_yellow(self):
        self.flight(fc_link_ok=False, link_ok=False)
        self.assertEqual(self.alerts(), [(monitor.RED, "FC LINK LOST")])
        self.flight(fc_link_ok=None, link_ok=False)
        self.assertEqual(self.alerts(), [(monitor.YELLOW, "FC LINK UNKNOWN")])

    def test_not_active_is_yellow_not_no_link(self):
        """Between configure and activate: an expected step, not a fault."""
        self.flight(active=False, link_ok=False)
        self.assertEqual(self.alerts(), [(monitor.YELLOW, "NOT ACTIVE")])

    def test_no_link_only_when_nothing_else_explains_it(self):
        self.flight(link_ok=False)  # e.g. the link to the ESP went stale
        self.assertEqual(self.alerts(), [(monitor.RED, "NO LINK")])
        self.flight(link_ok=None)
        self.assertEqual(self.alerts(), [(monitor.RED, "NO LINK")])
        self.feed("/flight/state", string(dict(FLIGHT_STANDIN, link_ok=False)))
        self.assertEqual(self.alerts(), [(monitor.RED, "NO LINK")])

    def test_severity_order(self):
        self.flight(
            estop=True,
            estop_reason="service",
            failsafe_output=True,
            fc_link_ok=False,
            active=False,
            link_ok=False,
        )
        self.assertEqual(
            self.alerts(),
            [
                (monitor.RED, "E-STOP: service"),
                (monitor.RED, "FAILSAFE"),
                (monitor.RED, "FC LINK LOST"),
                (monitor.YELLOW, "NOT ACTIVE"),
            ],
        )

    def test_stale_replaces_the_alerts_of_old_values(self):
        self.flight(estop=True, estop_reason="service", link_ok=False)
        self.age_flight(4.0)
        self.assertEqual(self.alerts(), [(monitor.RED, "NO /flight/state (4 s)")])

    def test_bad_json_is_an_alert(self):
        self.feed("/flight/state", String(data="{not json"))
        self.assertEqual(self.alerts(), [(monitor.RED, "BAD /flight/state")])

    def test_alert_line_under_the_title_without_colour(self):
        self.flight(estop=True, estop_reason="service", active=False, link_ok=False)
        with mock.patch.object(monitor.time, "strftime", return_value="12:00:00"):
            lines = monitor.table(self.monitor.snapshot())
        self.assertEqual(lines[1], "!! E-STOP: service  !! NOT ACTIVE")
        self.assertEqual(lines[2], "")
        self.assertNotIn("\033", "".join(lines))

    def test_no_alert_line_while_healthy(self):
        self.flight()
        lines = monitor.table(self.monitor.snapshot())
        self.assertEqual(lines[1], "")

    def test_old_values_stay_on_a_stale_row(self):
        """Decision 8A: the last state before it stopped stays visible."""
        self.flight()
        self.age_flight(4.0)
        lines = monitor.table(self.monitor.snapshot())
        self.assertIn("!! NO /flight/state (4 s)", lines[1])
        self.assertTrue(any(line.startswith("    link_ok") for line in lines))

    def test_colour_on_the_alert_line_and_the_flagged_fields(self):
        self.flight(estop=True, estop_reason="service", fc_link_ok=None, link_ok=False)
        lines = monitor.table(self.monitor.snapshot(), colour=True)
        self.assertEqual(
            lines[1],
            monitor.RED
            + "!! E-STOP: service"
            + monitor.RESET
            + "  "
            + monitor.YELLOW
            + "!! FC LINK UNKNOWN"
            + monitor.RESET,
        )
        estop = next(line for line in lines if "estop " in line)
        self.assertTrue(estop.startswith(monitor.RED), estop)
        fc_link = next(line for line in lines if "fc_link_ok" in line)
        self.assertTrue(fc_link.startswith(monitor.YELLOW), fc_link)
        armed = next(line for line in lines if "armed" in line)
        self.assertNotIn("\033", armed)

    def test_one_line_starts_with_the_alerts_no_colour(self):
        self.flight(estop=True, estop_reason="service", active=False, link_ok=False)
        line = monitor.one_line(self.monitor.snapshot())
        self.assertTrue(
            line.startswith("!! E-STOP: service !! NOT ACTIVE | flight: "), line
        )
        self.assertNotIn("\033", line)


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class AttitudeTests(MonitorTestCase):
    """Phase 3 of #227: /fc/attitude in whole degrees, REP-103 signs as sent."""

    def angles(self):
        return self.rows()["attitude"]["angles"]

    def test_signs_are_the_topics_own(self):
        """Right side 3 deg down, nose 10 deg up, turned 50 deg right."""
        self.feed("/fc/attitude", attitude(3, -10, -50))
        self.assertEqual(
            self.angles(),
            "roll: +3 deg    pitch: -10 deg    yaw: -50 deg (from start)",
        )
        # Left side down, nose down, turned left: every sign flips.
        self.feed("/fc/attitude", attitude(-3, 10, 50))
        self.assertEqual(
            self.angles(),
            "roll: -3 deg    pitch: +10 deg    yaw: +50 deg (from start)",
        )

    def test_whole_degrees_from_radians(self):
        # 0.1 rad = 5.73 deg -> +6
        self.assertEqual(monitor._degrees(0.1), "+6 deg")
        self.assertEqual(monitor._degrees(-0.1), "-6 deg")
        self.assertEqual(monitor._degrees(math.radians(179.6)), "+180 deg")

    def test_halves_round_away_from_zero(self):
        self.assertEqual(monitor._degrees(math.radians(2.5)), "+3 deg")
        self.assertEqual(monitor._degrees(math.radians(-2.5)), "-3 deg")
        self.assertEqual(monitor._degrees(math.radians(2.49)), "+2 deg")

    def test_zero_has_no_sign(self):
        for deg in (0.0, 0.4, -0.4):
            with self.subTest(deg=deg):
                self.assertEqual(monitor._degrees(math.radians(deg)), "0 deg")

    def test_noise_below_half_a_degree_keeps_the_text(self):
        """No redraw for sensor noise: the table text only changes on a whole degree."""
        self.feed("/fc/attitude", attitude(3.1, -9.8, -50.2))
        first = self.angles()
        self.feed("/fc/attitude", attitude(2.9, -10.3, -49.7))
        self.assertEqual(self.angles(), first)

    def test_one_line_under_the_row_heading(self):
        self.feed("/fc/attitude", attitude(3, -10, -50))
        lines = monitor.table(self.monitor.snapshot())
        heading = lines.index(
            next(line for line in lines if line.startswith("ATTITUDE"))
        )
        self.assertIn("/fc/attitude", lines[heading])
        self.assertEqual(
            lines[heading + 1],
            "    roll: +3 deg    pitch: -10 deg    yaw: -50 deg (from start)",
        )
        self.assertEqual(lines[heading + 2], "")

    def test_stale_after_one_second_keeps_the_last_angles(self):
        self.feed("/fc/attitude", attitude(3, -10, -50))
        when, fields = self.monitor.rows["attitude"]
        self.monitor.rows["attitude"] = (when - 2.0, fields)
        lines = monitor.table(self.monitor.snapshot())
        heading = next(line for line in lines if line.startswith("ATTITUDE"))
        self.assertTrue(heading.endswith("STALE 2 s"), heading)
        self.assertIn(
            "    roll: +3 deg    pitch: -10 deg    yaw: -50 deg (from start)", lines
        )

    def test_no_alert_from_attitude(self):
        self.feed("/fc/attitude", attitude(60, 60, 170))
        self.assertEqual(monitor.alerts(self.monitor.snapshot()), [])

    def test_not_in_the_one_line_output(self):
        self.feed("/fc/attitude", attitude(3, -10, -50))
        line = monitor.one_line(self.monitor.snapshot())
        self.assertNotIn("attitude", line)
        self.assertNotIn("roll", line)


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class BatteryTests(MonitorTestCase):
    """Phase 4 of #227: /fc/battery and the 3.5 V/cell land warning."""

    def line(self):
        return self.rows()["battery"]["line"]

    def alerts(self):
        return [(c, text) for c, text, _ in monitor.alerts(self.monitor.snapshot())]

    def hold_low(self, seconds):
        """Pretend the pack went low `seconds` ago."""
        self.monitor.low_since -= seconds

    def test_pack_and_per_cell_voltage(self):
        # 14.80 V / 4 cells = 3.70 V/cell
        self.feed("/fc/battery", battery(14.8))
        self.assertEqual(self.line(), "14.80 V    3.70 V/cell (4 cells)")

    def test_no_battery_on_usb_power(self):
        for msg in (battery(float("nan"), present=False), battery(float("nan"))):
            with self.subTest(present=msg.present):
                self.feed("/fc/battery", msg)
                self.assertEqual(self.line(), "no battery (USB power)")
                self.assertEqual(self.alerts(), [])

    def test_no_cells_shows_the_total_only(self):
        self.feed("/fc/battery", battery(14.8, cells=0))
        self.assertEqual(self.line(), "14.80 V")
        self.assertIsNone(self.monitor.low_since)

    def test_at_the_land_level_for_the_hold_time_alerts(self):
        self.feed("/fc/battery", battery(14.0))  # exactly 3.50 V/cell
        self.assertEqual(self.alerts(), [])  # not yet: needs the hold time
        self.hold_low(monitor.LOW_BATTERY_HOLD_SEC)
        self.assertEqual(self.alerts(), [(monitor.RED, "LOW BATTERY: 3.50 V/cell")])

    def test_above_the_land_level_never_alerts(self):
        self.feed("/fc/battery", battery(14.04))  # 3.51 V/cell
        self.assertIsNone(self.monitor.low_since)
        self.assertEqual(self.alerts(), [])

    def test_a_short_sag_does_not_alert(self):
        """Sag under throttle: low for a moment, then back up."""
        self.feed("/fc/battery", battery(13.8))  # 3.45 V/cell
        self.hold_low(1.5)
        self.feed("/fc/battery", battery(14.4))  # 3.60: the break restarts the hold
        self.assertIsNone(self.monitor.low_since)
        self.feed("/fc/battery", battery(13.8))
        self.hold_low(1.5)
        self.assertEqual(self.alerts(), [])

    def test_severity_order_and_no_link_still_shown(self):
        self.feed(
            "/flight/state",
            string(dict(FLIGHT, estop=True, estop_reason="service", link_ok=False)),
        )
        self.feed("/fc/battery", battery(13.8))
        self.hold_low(3.0)
        self.feed(
            "/flight/state",
            string(dict(FLIGHT, estop=True, estop_reason="service", active=False)),
        )
        self.assertEqual(
            self.alerts(),
            [
                (monitor.RED, "E-STOP: service"),
                (monitor.RED, "LOW BATTERY: 3.45 V/cell"),
                (monitor.YELLOW, "NOT ACTIVE"),
            ],
        )
        # LOW BATTERY does not explain a lost link: NO LINK stays.
        self.feed("/flight/state", string(dict(FLIGHT, link_ok=False)))
        self.assertEqual(
            self.alerts(),
            [(monitor.RED, "NO LINK"), (monitor.RED, "LOW BATTERY: 3.45 V/cell")],
        )

    def test_low_battery_without_flight_state(self):
        """fc_telemetry on the bench has no /flight/state: the alert still shows."""
        self.feed("/fc/battery", battery(13.8))
        self.hold_low(3.0)
        self.assertEqual(self.alerts(), [(monitor.RED, "LOW BATTERY: 3.45 V/cell")])

    def test_stale_row_gives_no_alert(self):
        self.feed("/fc/battery", battery(13.8))
        self.hold_low(3.0)
        when, fields = self.monitor.rows["battery"]
        self.monitor.rows["battery"] = (when - 2.0, fields)
        self.assertEqual(self.alerts(), [])

    def test_one_line_under_the_heading_red_when_low(self):
        self.feed("/fc/battery", battery(13.8))
        lines = monitor.table(self.monitor.snapshot())
        heading = lines.index(
            next(line for line in lines if line.startswith("BATTERY"))
        )
        self.assertIn("/fc/battery", lines[heading])
        self.assertEqual(lines[heading + 1], "    13.80 V    3.45 V/cell (4 cells)")
        self.assertEqual(lines[heading + 2], "")  # per_cell, low_for not shown
        self.hold_low(3.0)
        lines = monitor.table(self.monitor.snapshot(), colour=True)
        self.assertEqual(
            lines[heading + 2],  # the alert line moved every row down by one
            monitor.RED + "    13.80 V    3.45 V/cell (4 cells)" + monitor.RESET,
        )

    def test_not_in_the_one_line_output_but_the_alert_is(self):
        self.feed("/fc/battery", battery(14.8))
        self.assertNotIn("V/cell", monitor.one_line(self.monitor.snapshot()))
        self.feed("/fc/battery", battery(13.8))
        self.hold_low(3.0)
        line = monitor.one_line(self.monitor.snapshot())
        self.assertTrue(line.startswith("!! LOW BATTERY: 3.45 V/cell | "), line)
        self.assertNotIn("13.80 V", line)


class Publisher:
    """Plays the nodes' side: publishes the chain's topics, /flight/state,
    /fc/attitude and /fc/battery at 20 Hz."""

    def __init__(self):
        self.node = rclpy.create_node("monitor_test_publisher")
        self.pubs = {
            topic: self.node.create_publisher(String, topic, 10)
            for topic in (
                "/flight/state",
                "/gesture/raw",
                "/gesture/intent",
                "/mission/state",
            )
        }
        self.vel = self.node.create_publisher(
            TwistStamped, "/mission/velocity_target", 10
        )
        self.att = self.node.create_publisher(Vector3Stamped, "/fc/attitude", 10)
        self.bat = self.node.create_publisher(BatteryState, "/fc/battery", 10)
        self.messages = {}
        self.velocity = None
        self.attitude = None  # (roll, pitch, yaw) in degrees
        self.battery = None  # pack voltage, 4 cells
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.node.create_timer(0.05, self._tick)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def _tick(self):
        for topic, data in list(self.messages.items()):
            self.pubs[topic].publish(string(data))
        if self.velocity is not None:
            self.vel.publish(twist(*self.velocity))
        if self.attitude is not None:
            self.att.publish(attitude(*self.attitude))
        if self.battery is not None:
            self.bat.publish(battery(self.battery))

    def _spin(self):
        while not self.stop.is_set():
            self.executor.spin_once(timeout_sec=0.05)

    def close(self):
        self.stop.set()
        self.thread.join(2.0)
        self.executor.shutdown()
        self.node.destroy_node()


class MonitorProcess:
    """monitor.py as a program, on a pipe or on a pty."""

    def __init__(self, tty):
        env = dict(os.environ, ROS_DOMAIN_ID=str(TEST_DOMAIN_ID), PYTHONUNBUFFERED="1")
        self.master = None
        if tty:
            self.master, slave = pty.openpty()
            out = slave
        else:
            out = subprocess.PIPE
        self.proc = subprocess.Popen(
            [sys.executable, str(MONITOR)],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
        )
        if tty:
            os.close(slave)
        self.chunks = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        fd = self.master if self.master is not None else self.proc.stdout.fileno()
        while True:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                if self.proc.poll() is not None:
                    return
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:  # pty closed when the program exits
                return
            if not data:
                return
            self.chunks.append(data)

    @property
    def output(self):
        return b"".join(self.chunks).decode(errors="replace")

    def wait_for(self, condition, timeout=20.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and self.proc.poll() is None:
            if condition(self.output):
                return True
            time.sleep(0.05)
        return condition(self.output)

    def stop(self, sig):
        self.proc.send_signal(sig)
        try:
            code = self.proc.wait(10.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait()
        self.reader.join(2.0)
        return code

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.reader.join(2.0)
        if self.master is not None:
            os.close(self.master)
        elif self.proc.stdout:
            self.proc.stdout.close()


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class ProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.publisher = Publisher()
        self.addCleanup(self.publisher.close)

    def start(self, tty):
        process = MonitorProcess(tty)
        self.addCleanup(process.close)
        return process

    def test_without_a_terminal_one_line_per_change(self):
        process = self.start(tty=False)
        self.publisher.messages["/flight/state"] = FLIGHT
        self.publisher.messages["/gesture/intent"] = INTENT
        self.publisher.messages["/mission/state"] = STATE
        self.publisher.velocity = (0.0, -0.3, 0.0, 0.0)
        self.publisher.attitude = (3.0, -10.0, -50.0)
        self.publisher.battery = 14.8
        self.assertTrue(
            process.wait_for(
                lambda out: "flight: link_ok=True esp_state=STREAMING armed=True" in out
                and "intent: MOVE_RIGHT ok INDEX_RIGHT" in out
                and "mission: ENGAGED" in out
                and "velocity: right 0.30 m/s" in out
            ),
            process.output,
        )
        lines = process.output.splitlines()
        time.sleep(1.0)  # same messages: no new line
        self.assertEqual(process.output.splitlines(), lines)
        self.publisher.messages["/gesture/intent"] = dict(INTENT, intent="MOVE_LEFT")
        self.assertTrue(
            process.wait_for(lambda out: "intent: MOVE_LEFT" in out), process.output
        )
        self.assertEqual(process.stop(signal.SIGTERM), 0, process.output)
        self.assertNotIn("\033", process.output)
        self.assertNotIn("Traceback", process.output)
        self.assertNotIn("roll:", process.output)  # attitude stays out (8A)
        self.assertNotIn("V/cell", process.output)  # battery too (phase 4, 8A)

    def test_on_a_terminal_frames_only_when_something_changes(self):
        process = self.start(tty=True)
        self.publisher.messages["/gesture/intent"] = INTENT
        self.publisher.attitude = (3.0, -10.0, -50.0)  # same angles at 20 Hz
        self.publisher.battery = 14.8  # same voltage at 20 Hz
        self.assertTrue(
            process.wait_for(
                lambda out: "MOVE_RIGHT" in out
                and "roll: +3 deg    pitch: -10 deg    yaw: -50 deg (from start)" in out
                and "14.80 V    3.70 V/cell (4 cells)" in out
            ),
            process.output,
        )
        self.assertTrue(process.output.startswith(monitor.ENTER), process.output[:40])
        before = process.output.count(monitor.SYNC_ON)
        time.sleep(2.0)  # 20 refreshes, nothing new: only the clock (1 per s)
        self.assertLessEqual(process.output.count(monitor.SYNC_ON) - before, 3)
        self.assertEqual(process.stop(signal.SIGINT), 0, process.output)

        out = process.output
        self.assertTrue(out.endswith(monitor.LEAVE), repr(out[-40:]))
        frames = out[len(monitor.ENTER) : -len(monitor.LEAVE)].split(monitor.SYNC_ON)
        self.assertEqual(frames[0], "")
        for frame in frames[1:]:
            # Whole frame inside synchronized output, ending with a newline:
            # `docker compose up` holds back a line until it is complete.
            self.assertTrue(frame.endswith(monitor.SYNC_OFF + "\r\n"), repr(frame))
            self.assertIn(monitor.HOME, frame)
        self.assertNotIn("ms ago", out)
        self.assertNotIn("Traceback", out)

    def test_on_a_terminal_sigterm_shows_the_cursor_again(self):
        process = self.start(tty=True)
        self.assertTrue(
            process.wait_for(lambda out: "Ground pipeline monitor" in out),
            process.output,
        )
        self.assertEqual(process.stop(signal.SIGTERM), 0, process.output)
        self.assertTrue(process.output.endswith(monitor.LEAVE))


if __name__ == "__main__":
    unittest.main()
