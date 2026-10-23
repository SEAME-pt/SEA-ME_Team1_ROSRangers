"""The echo container's live table: drone/ground/docker/echo/monitor.py.

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

MONITOR = (
    Path(__file__).resolve().parents[2] / "ground" / "docker" / "echo" / "monitor.py"
)

if ros_available():
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from rclpy.executors import SingleThreadedExecutor
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
    "mode_reason": "hold_position",
    "intent": "MOVE_RIGHT",
    "reason": "ok",
    "gesture": "INDEX_RIGHT",
    "mask": 11,
    "airborne": True,
    "land_request": False,
    "limited": ["ramping:y"],
    "intent_age_ms": 12,
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
            ["gesture", "intent", "mission", "velocity"],
        )
        self.assertEqual([age for _, age, _ in snapshot], [None] * 4)

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
                "why": "hold_position",
                "mask": 11,
                "airborne": True,
                "limited": "ramping:y",
            },
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
            "gesture: - | intent: MOVE_RIGHT ok INDEX_RIGHT | mission: - "
            "| velocity: right 0.30 m/s",
        )
        self.feed("/mission/velocity_target", twist())
        self.assertIn("velocity: 0", monitor.one_line(self.monitor.snapshot()))


class Publisher:
    """Plays mission_node's side: publishes the chain's topics at 20 Hz."""

    def __init__(self):
        self.node = rclpy.create_node("monitor_test_publisher")
        self.pubs = {
            topic: self.node.create_publisher(String, topic, 10)
            for topic in ("/gesture/raw", "/gesture/intent", "/mission/state")
        }
        self.vel = self.node.create_publisher(
            TwistStamped, "/mission/velocity_target", 10
        )
        self.messages = {}
        self.velocity = None
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
        self.publisher.messages["/gesture/intent"] = INTENT
        self.publisher.messages["/mission/state"] = STATE
        self.publisher.velocity = (0.0, -0.3, 0.0, 0.0)
        self.assertTrue(
            process.wait_for(
                lambda out: "intent: MOVE_RIGHT ok INDEX_RIGHT" in out
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

    def test_on_a_terminal_frames_only_when_something_changes(self):
        process = self.start(tty=True)
        self.publisher.messages["/gesture/intent"] = INTENT
        self.assertTrue(
            process.wait_for(lambda out: "MOVE_RIGHT" in out), process.output
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
