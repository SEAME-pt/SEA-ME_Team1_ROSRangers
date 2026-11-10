"""Process tests for the flight_interface lifecycle node: run it as a program.

Runs `python3 -m flight_interface.link_node` (the same main() that `ros2 run`
starts) as a subprocess and checks:
  * it starts unconfigured;
  * Ctrl+C and SIGTERM are a normal stop: exit 0, no traceback, also after it
    was configured and activated (then it deactivates first: one last zero).

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.
"""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest

try:
    import rclpy
    from lifecycle_msgs.msg import Transition
    from lifecycle_msgs.srv import ChangeState
    from rclpy.executors import SingleThreadedExecutor
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them).
TEST_DOMAIN_ID = 93
NODE = [sys.executable, "-m", "flight_interface.link_node"]


class Proc:
    """The node as a subprocess; a thread collects its output."""

    def __init__(self):
        env = dict(os.environ, ROS_DOMAIN_ID=str(TEST_DOMAIN_ID), PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            NODE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        self.out = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.proc.stdout:
            self.out.append(line)

    def text(self):
        return "".join(self.out)

    def wait_for(self, text, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if text in self.text():
                return
            time.sleep(0.05)
        raise AssertionError(f"{text!r} not seen in:\n{self.text()}")

    def stop(self, sig):
        self.proc.send_signal(sig)
        code = self.proc.wait(timeout=10)
        self.reader.join(2.0)
        return code, self.text()

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class LinkNodeProcessTests(unittest.TestCase):
    def test_starts_unconfigured_and_ctrl_c_is_a_clean_stop(self):
        proc = Proc()
        try:
            proc.wait_for("unconfigured")
            code, out = proc.stop(signal.SIGINT)
        finally:
            proc.kill()
        self.assertEqual(code, 0, out)
        self.assertNotIn("Traceback", out)

    def test_sigterm_while_active_deactivates_first(self):
        proc = Proc()
        rclpy.init(domain_id=TEST_DOMAIN_ID)
        node = rclpy.create_node("link_node_process_probe")
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        try:
            proc.wait_for("unconfigured")
            client = node.create_client(ChangeState, "/flight_interface/change_state")
            self.assertTrue(client.wait_for_service(timeout_sec=10.0))
            for transition in (
                Transition.TRANSITION_CONFIGURE,
                Transition.TRANSITION_ACTIVATE,
            ):
                request = ChangeState.Request()
                request.transition.id = transition
                future = client.call_async(request)
                executor.spin_until_future_complete(future, timeout_sec=5.0)
                self.assertTrue(future.result().success)
            proc.wait_for("active: setpoints go out")
            code, out = proc.stop(signal.SIGTERM)
        finally:
            proc.kill()
            executor.shutdown()
            node.destroy_node()
            rclpy.shutdown()
        self.assertEqual(code, 0, out)
        self.assertIn("inactive: no more setpoints", out)
        self.assertNotIn("Traceback", out)

    def test_a_parameter_of_the_wrong_type_is_fatal_without_a_traceback(self):
        env = dict(os.environ, ROS_DOMAIN_ID=str(TEST_DOMAIN_ID))
        result = subprocess.run(
            NODE + ["--ros-args", "-p", "rate_hz:=50"],  # an int, not a double
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("FATAL", out)
        self.assertNotIn("Traceback", out)
