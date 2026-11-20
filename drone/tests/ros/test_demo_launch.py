"""`ros2 launch ground_bringup demo.launch.py camera:=false`, end to end.

Starts the demo mode as a subprocess without the camera (intent_node on the
topic input, mission_node, fake_flight_state, all with the mode's parameter
file) and plays gesture_node: JSON packets on /gesture/raw at 10 Hz, like
gesture_sender.py. Checks that both open hands,
shown after the hands were seen down, engage, an index finger moves the
target, and Ctrl+C stops everything cleanly: ros2 launch exits 0 and none of
its nodes is left running.

Launched with a copy of config/demo.yaml that sets airborne: false (also
checks that params_file reaches fake_flight_state).

Needs ROS 2 Jazzy sourced and intent_node, mission_node and ground_bringup
installed (colcon build, workspace sourced). Without them the test is skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then it fails.

Run with:  python3 -m pytest drone/tests/ros -v        (from the repo root, ROS sourced)
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import yaml
from support import kill_group, ros_available, wait_group_gone

if ros_available():
    import rclpy
    from geometry_msgs.msg import TwistStamped
    from rclpy.executors import SingleThreadedExecutor
    from std_msgs.msg import String
else:
    rclpy = None


def installed(*packages):
    try:
        from ament_index_python.packages import get_package_share_directory

        for package in packages:
            get_package_share_directory(package)
    except (ImportError, LookupError):
        if os.environ.get("REQUIRE_ROS_TESTS"):
            raise
        return False
    return shutil.which("ros2") is not None


# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 90


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
@unittest.skipIf(
    rclpy is not None
    and not installed("intent_node", "mission_node", "ground_bringup"),
    "intent_node, mission_node or ground_bringup not installed",
)
class DemoLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init(domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def params_file(self):
        """The installed demo.yaml, with the pretend drone on the ground."""
        from ament_index_python.packages import get_package_share_directory

        share = Path(get_package_share_directory("ground_bringup"))
        params = yaml.safe_load((share / "config" / "demo.yaml").read_text())
        params["fake_flight_state"]["ros__parameters"]["airborne"] = False
        folder = tempfile.TemporaryDirectory(prefix="demo-launch-test-")
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "demo.yaml"
        path.write_text(yaml.safe_dump(params))
        return path

    def setUp(self):
        env = dict(os.environ, ROS_DOMAIN_ID=str(TEST_DOMAIN_ID), PYTHONUNBUFFERED="1")
        params = self.params_file()
        # Own process group, stopped as a whole (see support.py). stdin not
        # a terminal: ros2 launch behaves the same wherever the tests run.
        self.launch = subprocess.Popen(
            [
                "ros2",
                "launch",
                "ground_bringup",
                "demo.launch.py",
                "camera:=false",
                f"params_file:={params}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            start_new_session=True,
        )
        self.addCleanup(kill_group, self.launch.pid)
        self.lines = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.probe = rclpy.create_node("demo_launch_test_probe")
        self.raw_pub = self.probe.create_publisher(String, "/gesture/raw", 10)
        self.mode = None
        self.reason = None
        self.airborne = None
        self.vy = None
        self.probe.create_subscription(String, "/mission/state", self._on_state, 10)
        self.probe.create_subscription(
            TwistStamped, "/mission/velocity_target", self._on_velocity, 10
        )
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.probe)

    def tearDown(self):
        kill_group(self.launch.pid)  # its nodes too, not only ros2 launch
        self.launch.wait()
        self.reader.join(2.0)
        self.launch.stdout.close()
        self.executor.shutdown()
        self.probe.destroy_node()

    def _read(self):
        for line in self.launch.stdout:
            self.lines.append(line)

    def _on_state(self, msg):
        state = json.loads(msg.data)
        self.mode = state["mode"]
        self.reason = state["reason"]
        self.airborne = state["airborne"]

    def _on_velocity(self, msg):
        self.vy = msg.twist.linear.y

    def show(self, gesture, until, timeout):
        """Send `gesture` packets at 10 Hz until until()."""
        end = time.monotonic() + timeout
        next_packet = time.monotonic()
        while time.monotonic() < end:
            if until():
                return True
            if time.monotonic() >= next_packet:
                packet = {"gesture": gesture, "t": time.time(), "xy": None}
                self.raw_pub.publish(String(data=json.dumps(packet)))
                next_packet += 0.1
            self.executor.spin_once(timeout_sec=0.01)
        return bool(until())

    def test_open_hand_engages_index_moves_and_ctrl_c_stops(self):
        output = lambda: "".join(self.lines)  # noqa: E731
        # Hands down first: hands already up when everything starts never
        # engage, so intent_node must have reported "no hand" once.
        self.assertTrue(
            self.show(
                None,
                lambda: self.mode == "WAITING"
                and self.reason in ("no_hand", "no_hand_long"),
                30.0,
            ),
            output(),
        )
        self.assertIs(
            self.airborne,
            False,
            "airborne: false in params_file did not reach fake_flight_state",
        )
        self.assertTrue(
            self.show(
                "L:OPEN_HAND_UP+R:OPEN_HAND_UP", lambda: self.mode == "ENGAGED", 5.0
            ),
            output(),
        )
        self.assertTrue(
            self.show(
                "L:INDEX_RIGHT+R:NONE",
                lambda: self.vy is not None and self.vy < -0.1,
                5.0,
            ),
            output(),
        )
        os.killpg(self.launch.pid, signal.SIGINT)  # Ctrl+C in a terminal
        self.assertEqual(self.launch.wait(15.0), 0, output())
        self.assertTrue(wait_group_gone(self.launch.pid), "a node outlived ros2 launch")
        self.reader.join(2.0)
        self.assertNotIn("Traceback", output())
        self.assertNotIn("[ERROR]", output())


if __name__ == "__main__":
    unittest.main()
