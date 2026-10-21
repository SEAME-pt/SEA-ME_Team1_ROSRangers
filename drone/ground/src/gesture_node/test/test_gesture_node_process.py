"""Process tests for gesture_node: run it as a program, check how it ends.

Runs gesture_node's real main() through test/fake_camera.py (fake camera, no
window) as a subprocess and checks its exit code and output:
  * q in the window, Ctrl+C and SIGTERM (`docker stop`) are a normal stop:
    exit 0 within seconds, no traceback. Also Ctrl+C twice, as `ros2 launch`
    forwards one more.
  * Camera missing or lost: exit 1 with an error log, no traceback.
  * While it runs, the gesture arrives on /gesture/raw.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select gesture_node --event-handlers console_direct+
or:        python3 -m unittest test.test_gesture_node_process -v   (package folder, ROS sourced)
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 85
FAKE_CAMERA = Path(__file__).with_name("fake_camera.py")
# fake_camera.py runs as a script, so its sys.path starts at test/. Add the
# package folder last: an installed gesture_node (colcon, CI) still wins, so
# coverage measures one copy, and `make unit` works without colcon.
PACKAGE_DIR = FAKE_CAMERA.parent.parent


class NodeProcess:
    """A node started as a subprocess, its output collected line by line."""

    def __init__(self, cmd):
        pythonpath = os.pathsep.join(
            p for p in (os.environ.get("PYTHONPATH"), str(PACKAGE_DIR)) if p
        )
        env = dict(
            os.environ,
            ROS_DOMAIN_ID=str(TEST_DOMAIN_ID),
            PYTHONUNBUFFERED="1",
            PYTHONPATH=pythonpath,
        )
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
        )
        self.lines = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line)

    @property
    def output(self):
        return "".join(self.lines)

    def wait_for_output(self, text, timeout=20.0):
        end = time.monotonic() + timeout
        while text not in self.output and self.proc.poll() is None:
            if time.monotonic() > end:
                break
            time.sleep(0.05)
        return text in self.output

    def finish(self, timeout=10.0):
        """Exit code, or None (and killed) if it still runs after `timeout` s."""
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            code = None
        self.reader.join(2.0)
        self.proc.stdout.close()
        return code

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.reader.join(2.0)
        self.proc.stdout.close()


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class GestureNodeProcessTests(unittest.TestCase):
    def start(self, mode):
        node = NodeProcess([sys.executable, str(FAKE_CAMERA), mode])
        self.addCleanup(node.kill)
        return node

    def assertCleanStop(self, *signals):
        node = self.start("run")
        self.assertTrue(node.wait_for_output("Reading camera 0"), node.output)
        time.sleep(0.5)  # a few frames
        for sig in signals:
            node.proc.send_signal(sig)
            time.sleep(0.05)
        self.assertEqual(node.finish(), 0, node.output)
        self.assertNotIn("Traceback", node.output)

    def test_ctrl_c_stops_cleanly(self):
        self.assertCleanStop(signal.SIGINT)

    def test_double_ctrl_c_stops_cleanly(self):
        self.assertCleanStop(signal.SIGINT, signal.SIGINT)

    def test_sigterm_stops_cleanly(self):
        self.assertCleanStop(signal.SIGTERM)

    def test_q_stops_cleanly(self):
        node = self.start("quit")
        self.assertEqual(node.finish(), 0, node.output)
        self.assertIn("q pressed", node.output)
        self.assertNotIn("Traceback", node.output)

    def test_missing_camera_exits_1(self):
        node = self.start("no_camera")
        self.assertEqual(node.finish(), 1, node.output)
        self.assertIn("Cannot open camera 0", node.output)
        self.assertNotIn("Traceback", node.output)

    def test_lost_camera_exits_1(self):
        node = self.start("camera_lost")
        self.assertEqual(node.finish(), 1, node.output)
        self.assertIn("Camera 0 stopped delivering frames", node.output)
        self.assertNotIn("Traceback", node.output)

    def test_gesture_arrives_on_gesture_raw(self):
        context = rclpy.Context()
        rclpy.init(context=context, domain_id=TEST_DOMAIN_ID)
        self.addCleanup(rclpy.try_shutdown, context=context)
        probe = rclpy.create_node("gesture_process_probe", context=context)
        self.addCleanup(probe.destroy_node)
        raw = []
        probe.create_subscription(
            String, "/gesture/raw", lambda m: raw.append(json.loads(m.data)), 10
        )
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(probe)
        self.addCleanup(executor.shutdown)

        node = self.start("run")
        end = time.monotonic() + 20
        while len(raw) < 3 and time.monotonic() < end:
            executor.spin_once(timeout_sec=0.05)
        self.assertGreaterEqual(len(raw), 3, node.output)
        self.assertEqual({m["gesture"] for m in raw}, {"INDEX_RIGHT"})


if __name__ == "__main__":
    unittest.main()
