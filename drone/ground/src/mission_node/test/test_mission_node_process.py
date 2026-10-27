"""Process tests for mission_node: run it as a program, check how it ends.

Runs `python3 -m mission_node.<executable>` (the same main() that `ros2 run`
and the Docker image start) as a subprocess and checks its exit code, its
output and, for mission_node, its last message:
  * Ctrl+C and SIGTERM (`docker stop`) are a normal stop: exit 0 within
    seconds, no traceback, and the last /mission/velocity_target is zero even
    while the operator still shows MOVE_RIGHT.
  * A parameter that is not a number: exit 1 with a FATAL log line naming it,
    no traceback.
  * `ros2 launch mission_node mission_demo.launch.py` runs the pretend demo
    up to ENGAGED and a MOVE_RIGHT target, and Ctrl+C stops all of it cleanly.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.
The launch test also needs the package installed (colcon build, workspace
sourced); without it, it is skipped unless REQUIRE_ROS_TESTS=1.

Run with:  colcon test --packages-select mission_node --event-handlers console_direct+
or:        python3 -m unittest test.test_mission_node_process -v   (package folder, ROS sourced)
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import unittest

try:
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

from mission_node.fake_inputs import flight_state_json, intent_json


def package_installed():
    """True if `ros2 launch mission_node ...` can find the package."""
    try:
        from ament_index_python.packages import get_package_share_directory

        share = get_package_share_directory("mission_node")
    except (ImportError, LookupError):
        if os.environ.get("REQUIRE_ROS_TESTS"):
            raise
        return False
    found = os.path.isfile(os.path.join(share, "launch", "mission_demo.launch.py"))
    return found and shutil.which("ros2") is not None


# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 88
ZERO = (0.0, 0.0, 0.0, 0.0)


def module(name):
    return [sys.executable, "-m", f"mission_node.{name}", "--ros-args"]


class NodeProcess:
    """A node started as a subprocess, its output collected line by line.

    In its own process group: `ros2 launch` and its nodes can then be stopped
    together like Ctrl+C in a terminal (stop_group), and killed together
    (kill). Signalling only `ros2 launch` leaves its nodes running when its
    stdin is a terminal (it assumes they got Ctrl+C too), and SIGKILL is never
    passed on."""

    def __init__(self, cmd):
        env = dict(os.environ, ROS_DOMAIN_ID=str(TEST_DOMAIN_ID), PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            start_new_session=True,
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

    def stop_group(self, sig=signal.SIGINT):
        os.killpg(self.proc.pid, sig)

    def group_gone(self, timeout=5.0):
        """True once no process of the group is left (or after `timeout` s)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                os.killpg(self.proc.pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def kill(self):
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.proc.wait()
        self.reader.join(2.0)
        self.proc.stdout.close()


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class ProcessTestCase(unittest.TestCase):
    def start(self, name, *params):
        args = [arg for param in params for arg in ("-p", param)]
        node = NodeProcess(module(name) + args)
        self.addCleanup(node.kill)
        return node

    def assertCleanStop(self, node, *signals):
        for sig in signals:
            node.proc.send_signal(sig)
            time.sleep(0.05)
        self.assertEqual(node.finish(), 0, node.output)
        self.assertNotIn("Traceback", node.output)


class MissionNodeProcessTests(ProcessTestCase):
    def started(self, *params):
        node = self.start("mission_node", *params)
        self.assertTrue(node.wait_for_output("Reading /gesture/intent"), node.output)
        return node

    def test_ctrl_c_stops_cleanly(self):
        self.assertCleanStop(self.started(), signal.SIGINT)

    def test_double_ctrl_c_stops_cleanly(self):
        self.assertCleanStop(self.started(), signal.SIGINT, signal.SIGINT)

    def test_sigterm_stops_cleanly(self):
        self.assertCleanStop(self.started(), signal.SIGTERM)

    def test_integer_for_a_decimal_parameter_starts(self):
        self.assertCleanStop(self.started("intent_lost_sec:=3"), signal.SIGINT)

    def test_parameter_that_is_not_a_number_exits_1(self):
        node = self.start("mission_node", "max_lateral_mps:=fast")
        self.assertEqual(node.finish(), 1, node.output)
        self.assertIn("FATAL", node.output)
        self.assertIn("max_lateral_mps", node.output)
        self.assertNotIn("Traceback", node.output)


class ProbeTestCase(ProcessTestCase):
    """rclpy in the test domain and a probe on the mission topics."""

    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.probe = rclpy.create_node("mission_process_test_probe")
        self.intent_pub = self.probe.create_publisher(String, "/gesture/intent", 10)
        self.flight_pub = self.probe.create_publisher(String, "/flight/state", 10)
        self.velocities = []
        self.mode = None
        self.probe.create_subscription(
            TwistStamped,
            "/mission/velocity_target",
            lambda m: self.velocities.append((m.twist.linear.y, m)),
            10,
        )
        self.probe.create_subscription(String, "/mission/state", self._on_state, 10)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.probe)
        self.intent = "NEUTRAL"

    def tearDown(self):
        self.executor.shutdown()
        self.probe.destroy_node()

    def _on_state(self, msg):
        self.mode = json.loads(msg.data)["mode"]

    def run_probe(self, until, timeout):
        """Publish self.intent and an OK flight state at 20 Hz until until()."""
        end = time.monotonic() + timeout
        next_publish = time.monotonic()
        while time.monotonic() < end:
            if until():
                return True
            if time.monotonic() >= next_publish:
                self.intent_pub.publish(String(data=intent_json(self.intent, "ok")))
                self.flight_pub.publish(
                    String(data=flight_state_json(True, "STREAMING", True, 11, True))
                )
                next_publish += 0.05
            self.executor.spin_once(timeout_sec=0.002)
        return bool(until())


class LastTargetTests(ProbeTestCase):
    """The probe keeps showing MOVE_RIGHT while the node is stopped, so the
    only way to receive a zero target at the end is stop_output()."""

    def assertLastTargetIsZero(self, sig):
        node = self.start("mission_node")
        self.assertTrue(
            self.run_probe(lambda: self.mode == "WAITING", 20.0), node.output
        )
        self.intent = "ENGAGE"
        self.assertTrue(
            self.run_probe(lambda: self.mode == "ENGAGED", 3.0), node.output
        )
        self.intent = "MOVE_RIGHT"
        self.assertTrue(
            self.run_probe(lambda: self.velocities[-1][0] <= -0.3 + 1e-9, 3.0),
            node.output,
        )
        node.proc.send_signal(sig)
        self.run_probe(lambda: node.proc.poll() is not None, 10.0)
        self.run_probe(lambda: False, 0.5)  # let the last message arrive
        self.assertEqual(node.finish(), 0, node.output)
        self.assertNotIn("Traceback", node.output)
        last, before = self.velocities[-1][1], self.velocities[-2][1]
        self.assertEqual(
            (last.twist.linear.x, last.twist.linear.y, last.twist.linear.z),
            (0.0, 0.0, 0.0),
        )
        self.assertEqual(last.twist.angular.z, 0.0)
        self.assertEqual(last.header.frame_id, "base_link")
        self.assertLess(before.twist.linear.y, 0.0)  # it was moving until the stop

    def test_last_target_is_zero_after_sigterm(self):
        self.assertLastTargetIsZero(signal.SIGTERM)

    def test_last_target_is_zero_after_ctrl_c(self):
        self.assertLastTargetIsZero(signal.SIGINT)


class FakeNodeProcessTests(ProcessTestCase):
    def test_fake_flight_state_stops_cleanly(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                node = self.start("fake_flight_state")
                self.assertTrue(
                    node.wait_for_output("PRETEND flight state"), node.output
                )
                self.assertCleanStop(node, sig)

    def test_fake_intent_stops_cleanly(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                node = self.start("fake_intent")
                self.assertTrue(node.wait_for_output("Playing the demo"), node.output)
                self.assertCleanStop(node, sig)


@unittest.skipIf(
    rclpy is not None and not package_installed(), "mission_node not installed"
)
class DemoLaunchTests(ProbeTestCase):
    """`ros2 launch mission_node mission_demo.launch.py` end to end. The
    launch file brings its own pretend inputs: the probe only listens."""

    def test_demo_engages_moves_and_stops_cleanly(self):
        launch = NodeProcess(
            ["ros2", "launch", "mission_node", "mission_demo.launch.py"]
        )
        self.addCleanup(launch.kill)

        def spin_until(condition, timeout):
            end = time.monotonic() + timeout
            while time.monotonic() < end and not condition():
                self.executor.spin_once(timeout_sec=0.01)
            return bool(condition())

        # Demo script: NEUTRAL 2 s, ENGAGE 2 s, MOVE_RIGHT from 4 s
        self.assertTrue(spin_until(lambda: self.mode == "ENGAGED", 20.0), launch.output)
        self.assertTrue(
            spin_until(lambda: self.velocities and self.velocities[-1][0] < -0.1, 6.0),
            launch.output,
        )
        launch.stop_group(signal.SIGINT)  # Ctrl+C in a terminal
        self.assertEqual(launch.finish(15.0), 0, launch.output)
        self.assertTrue(launch.group_gone(), "a node outlived ros2 launch")
        self.assertNotIn("Traceback", launch.output)
        self.assertNotIn("[ERROR]", launch.output)


if __name__ == "__main__":
    unittest.main()
