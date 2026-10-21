"""Process tests for intent_node: run it as a program, check how it ends.

Runs `python3 -m intent_node.intent_node` (the same main() that `ros2 run`
and the Docker image start) as a subprocess and checks its exit code and
output:
  * Ctrl+C and SIGTERM (`docker stop`) are a normal stop: exit 0 within
    seconds, no traceback. Also Ctrl+C twice, as `ros2 launch` forwards one
    more.
  * Bad configuration (unknown input_source, UDP port in use): exit 1 with a
    FATAL log line, no traceback.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select intent_node --event-handlers console_direct+
or:        python3 -m unittest test.test_intent_node_process -v   (package folder, ROS sourced)
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest

try:
    import rclpy  # noqa: F401  (only to know whether ROS 2 is sourced)
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 84
NODE = [sys.executable, "-m", "intent_node.intent_node", "--ros-args"]


class NodeProcess:
    """A node started as a subprocess, its output collected line by line."""

    def __init__(self, cmd):
        env = dict(os.environ, ROS_DOMAIN_ID=str(TEST_DOMAIN_ID), PYTHONUNBUFFERED="1")
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
class IntentNodeProcessTests(unittest.TestCase):
    def start(self, *params):
        args = [arg for param in params for arg in ("-p", param)]
        node = NodeProcess(NODE + args)
        self.addCleanup(node.kill)
        return node

    def assertCleanStop(self, *signals):
        node = self.start("input_source:=topic")
        self.assertTrue(node.wait_for_output("Listening for"), node.output)
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

    def test_unknown_input_source_exits_1(self):
        node = self.start("input_source:=bogus")
        self.assertEqual(node.finish(), 1, node.output)
        self.assertIn('input_source must be "udp" or "topic"', node.output)
        self.assertNotIn("Traceback", node.output)

    def test_udp_port_in_use_exits_1(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
            busy.bind(("127.0.0.1", 0))
            port = busy.getsockname()[1]
            node = self.start(f"udp_port:={port}")
            code = node.finish()
        self.assertEqual(code, 1, node.output)
        self.assertIn(f"Cannot listen on UDP 127.0.0.1:{port}", node.output)
        self.assertNotIn("Traceback", node.output)


if __name__ == "__main__":
    unittest.main()
