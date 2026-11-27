"""Process tests for controller_node: run it as a program, check how it ends.

Runs `python3 -m controller_node.controller_node` (the same main() that
`ros2 run` and the Docker image start) as a subprocess:
  * Ctrl+C and SIGTERM (`docker stop`) are a normal stop: exit 0 within
    seconds, no traceback, and the last /control/attitude_setpoint is
    inactive with reason "stopped".
  * A bad parameter: exit 1 with a FATAL log line naming it, no traceback.
  * Its section of a mode file reaches it (`controller_node:`, F15).

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select controller_node --event-handlers console_direct+
or:        python3 -m pytest test -v   (package folder, ROS sourced)
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
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

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them).
TEST_DOMAIN_ID = 80
PACKAGE = Path(__file__).resolve().parents[1]


def start(*ros_args):
    env = dict(os.environ, ROS_DOMAIN_ID=str(TEST_DOMAIN_ID), PYTHONUNBUFFERED="1")
    # The package folder first, so the source is tested without installing.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PACKAGE), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    command = [sys.executable, "-m", "controller_node.controller_node"]
    if ros_args:
        command = command + ["--ros-args", *ros_args]
    return subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
    )


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class ProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.node = rclpy.create_node("controller_process_probe")
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.received = []
        self.node.create_subscription(
            String,
            "/control/attitude_setpoint",
            lambda msg: self.received.append(json.loads(msg.data)),
            10,
        )

    def tearDown(self):
        self.executor.shutdown()
        self.node.destroy_node()

    def spin_until(self, condition, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not condition():
            self.executor.spin_once(timeout_sec=0.05)
        return condition()

    def stop_with(self, sig):
        process = start("-p", "hover_throttle:=0.7", "-p", "mask:=15")
        try:
            self.assertTrue(
                self.spin_until(lambda: len(self.received) >= 10, 15.0),
                "no setpoints from the node",
            )
            process.send_signal(sig)
            output, _ = process.communicate(timeout=10)
            self.spin_until(lambda: self.received[-1]["reason"] == "stopped", 2.0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(process.returncode, 0, output)
        self.assertNotIn("Traceback", output)
        self.assertEqual(self.received[-1]["reason"], "stopped")
        self.assertFalse(self.received[-1]["active"])

    def test_sigterm_is_a_clean_stop(self):
        self.stop_with(signal.SIGTERM)

    def test_ctrl_c_is_a_clean_stop(self):
        self.stop_with(signal.SIGINT)

    def test_bad_parameter_exits_1(self):
        process = start("-p", "hover_throttle:=fast")
        output, _ = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 1, output)
        self.assertIn("FATAL", output)
        self.assertIn("hover_throttle", output)
        self.assertNotIn("Traceback", output)

    def test_mode_file_section_reaches_the_node(self):
        # F15: mask and hover_throttle in the node's own section.
        folder = tempfile.TemporaryDirectory(prefix="controller-test-")
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "mode.yaml"
        path.write_text(
            "controller_node:\n  ros__parameters:\n    mask: 11\n    hover_throttle: 0.6\n"
        )
        process = start("--params-file", str(path))
        try:
            self.assertTrue(
                self.spin_until(lambda: len(self.received) >= 3, 15.0),
                "no setpoints from the node",
            )
        finally:
            process.send_signal(signal.SIGTERM)
            output, _ = process.communicate(timeout=10)
        self.assertIn("mask 11, hover throttle 0.6", output)
        # hover measured, no velocity yet: the reason is no_velocity.
        self.assertEqual(self.received[0]["reason"], "no_velocity")


if __name__ == "__main__":
    unittest.main()
