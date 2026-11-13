"""Process tests for the flight_interface node with the ESP backend, and for
its launch file: run them as programs, the way the operator does.

  * `python3 -m flight_interface.link_node` (the same main() as `ros2 run`)
    with backend:=esp and the PSK in SEAME_PSK, against the fake ESP in this
    test process: configure + activate over the lifecycle services opens a
    session and streams neutral setpoints (mode 0 until C1), never ARM.
    SIGTERM deactivates (one last neutral) and ends the session with BYE
    (decision K3). Exit 0, no traceback, and the PSK never in the output.
  * backend:=esp without a PSK: configure fails and says why, the node stays
    up unconfigured and nothing reaches the ESP. Ctrl+C exits 0.
  * `ros2 launch flight_interface flight_interface.launch.py`: the node
    starts unconfigured with the loopback backend (no drone), and Ctrl+C
    stops all of it cleanly. The launch file has no respawn (decision A6,
    checked statically in test_launch_file.py).

Needs ROS 2 Jazzy sourced and sd_protocol. Without rclpy the tests are
skipped, unless REQUIRE_ROS_TESTS=1 is set (CI sets it). The launch test also
needs the package installed (colcon build, workspace sourced).
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
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

try:
    from fake_esp import TEST_PSK, FakeEsp
except ImportError:  # no sd_protocol
    FakeEsp = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them).
TEST_DOMAIN_ID = 96
NODE = [sys.executable, "-m", "flight_interface.link_node"]
LAUNCH = ["ros2", "launch", "flight_interface", "flight_interface.launch.py"]


def package_installed():
    """True if `ros2 launch flight_interface ...` can find the launch file."""
    try:
        from ament_index_python.packages import get_package_share_directory

        share = get_package_share_directory("flight_interface")
    except (ImportError, LookupError):
        if os.environ.get("REQUIRE_ROS_TESTS"):
            raise
        return False
    found = os.path.isfile(os.path.join(share, "launch", "flight_interface.launch.py"))
    return found and shutil.which("ros2") is not None


class Proc:
    """A program in its own process group; a thread collects its output.
    `ros2 launch` and its node are stopped and killed together."""

    def __init__(self, cmd, env):
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

    def text(self):
        return "".join(self.lines)

    def wait_for(self, text, timeout=15.0):
        end = time.monotonic() + timeout
        while text not in self.text() and time.monotonic() < end:
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        if text not in self.text():
            raise AssertionError(f"{text!r} not seen in:\n{self.text()}")

    def finish(self, timeout=15.0):
        """Exit code, or None (and killed) if it still runs after `timeout` s."""
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            return None
        self.reader.join(2.0)
        return code

    def group_gone(self, timeout=5.0):
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


class LifecycleTestCase(unittest.TestCase):
    """rclpy in the test domain, a lifecycle client, and a clean HOME (no
    ~/.config/seame-drone/secrets.json of the person running the tests)."""

    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def setUp(self):
        self.fake = None
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.env = dict(
            os.environ,
            ROS_DOMAIN_ID=str(TEST_DOMAIN_ID),
            PYTHONUNBUFFERED="1",
            HOME=home.name,
        )
        self.env.pop("SEAME_PSK", None)
        self.probe = rclpy.create_node("flight_interface_esp_process_probe")
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.probe)
        self.lifecycle = self.probe.create_client(
            ChangeState, "/flight_interface/change_state"
        )
        self.addCleanup(self._close_probe)

    def _close_probe(self):
        self.executor.shutdown()
        self.probe.destroy_node()

    def step(self, seconds, until=None):
        """Spin the probe (and step the fake ESP) for `seconds`, or until
        `until()` is true. Returns until() (True without one)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.fake is not None:
                self.fake.step(time.monotonic())
            self.executor.spin_once(timeout_sec=0.005)
            if until is not None and until():
                return True
        return until() if until is not None else True

    def transition(self, transition_id):
        self.assertTrue(self.lifecycle.wait_for_service(timeout_sec=10.0))
        request = ChangeState.Request()
        request.transition.id = transition_id
        future = self.lifecycle.call_async(request)
        self.assertTrue(self.step(10.0, until=future.done))
        return future.result().success


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
@unittest.skipIf(FakeEsp is None, "sd_protocol not installed")
class EspProcessTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeEsp()
        self.addCleanup(self.fake.close)

    def start(self):
        params = [
            "backend:=esp",
            "esp_host:=127.0.0.1",
            f"esp_port:={self.fake.addr[1]}",
        ]
        cmd = NODE + ["--ros-args"] + [a for p in params for a in ("-p", p)]
        proc = Proc(cmd, self.env)
        self.addCleanup(proc.kill)
        proc.wait_for("unconfigured")
        return proc

    def test_streams_neutral_and_sigterm_ends_the_session_with_bye(self):
        self.env["SEAME_PSK"] = TEST_PSK.hex()
        proc = self.start()
        self.assertTrue(self.transition(Transition.TRANSITION_CONFIGURE), proc.text())
        self.assertTrue(self.transition(Transition.TRANSITION_ACTIVATE), proc.text())
        self.assertTrue(
            self.step(5.0, until=lambda: self.fake.setpoints() > 10), proc.text()
        )
        self.assertEqual(self.fake.hellos, 1)
        self.assertEqual(set(self.fake.setpoint_modes), {0})  # neutral until C1

        proc.proc.send_signal(signal.SIGTERM)
        self.step(10.0, until=lambda: proc.proc.poll() is not None)
        self.step(0.2)  # the fake reads what the node sent last
        code, out = proc.finish(), proc.text()
        self.assertEqual(code, 0, out)
        self.assertIn("inactive: no more setpoints", out)
        self.assertEqual(self.fake.byes_received, 1, out)  # K3
        self.assertEqual(self.fake.arm_requests, 0)
        self.assertNotIn("Traceback", out)
        self.assertNotIn(TEST_PSK.hex(), out)

    def test_without_a_psk_configure_fails_and_says_why(self):
        proc = self.start()
        self.assertFalse(self.transition(Transition.TRANSITION_CONFIGURE))
        proc.wait_for("no valid PSK")
        self.step(0.5)
        self.assertEqual(self.fake.received, [])  # nothing reached the ESP
        self.assertIsNone(proc.proc.poll())  # still up, unconfigured
        proc.proc.send_signal(signal.SIGINT)
        code, out = proc.finish(), proc.text()
        self.assertEqual(code, 0, out)
        self.assertNotIn("Traceback", out)


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
@unittest.skipIf(rclpy is not None and not package_installed(), "not installed")
class LaunchFileTests(LifecycleTestCase):
    def test_starts_the_loopback_node_unconfigured_and_ctrl_c_stops_it(self):
        proc = Proc(LAUNCH, self.env)
        self.addCleanup(proc.kill)
        proc.wait_for("unconfigured")
        self.assertTrue(self.transition(Transition.TRANSITION_CONFIGURE), proc.text())
        proc.wait_for("configured: backend loopback")  # no drone by default
        os.killpg(proc.proc.pid, signal.SIGINT)  # Ctrl+C in a terminal
        code, out = proc.finish(), proc.text()
        self.assertEqual(code, 0, out)
        self.assertTrue(proc.group_gone(), "the node outlived ros2 launch")
        self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()
