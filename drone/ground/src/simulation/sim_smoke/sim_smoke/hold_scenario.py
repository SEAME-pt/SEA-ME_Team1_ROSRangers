#!/usr/bin/env python3
"""sim_hold_scenario: the SITL hold test, REQ-NAV-001 (SIM ONLY, #199 Phase 8).

make sim-hold runs it in its own container once the px4 container is healthy
(PX4 armed in Offboard at the take-off height, the controller flying). Like
make sim-smoke it starts its own intent_node and drives the chain through
/gesture/raw only (ADR-025 F26):

    no hands -> ENGAGE -> descend (INDEX_DOWN) until Gazebo's height reads
    1.0 m -> no hands: settle, then hold zero velocity for 60 s

Pass/fail is judged on Gazebo's ground truth (the model's world pose, read
with gz-transport, GZ_IP=127.0.0.1 as PX4 starts Gazebo) from the start point
of the hold (hold_checks.py). PX4's estimate is logged next to it, not judged.
Writes a rosbag, truth.csv (the whole run) and summary.md to
$LOG_DIR/sim-hold/<start time>/. Exit code 0 only if every check passed.

    sim_hold_scenario [--out DIR] [--hold-sec 60]
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

import rclpy
from ament_index_python.packages import get_package_prefix
from gz.msgs10.pose_v_pb2 import Pose_V
from gz.transport13 import Node as GzNode
from px4_msgs.msg import VehicleLocalPosition
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from sim_smoke import hold_checks as hc
from sim_smoke import smoke_checks as sc
from sim_smoke.smoke_scenario import ENGAGE, GESTURE_HZ, NO_HANDS, READY_TIMEOUT_SEC

DESCEND = "L:NONE+R:INDEX_DOWN"
MODEL = "x500_0"  # PX4's gz_x500 model; its pose/info entry is the world pose
TRUTH_TOPIC = "/world/default/pose/info"
# The descent (0.2 m/s, mission_node's vertical limit) stops this much above
# 1.0 m: the zero takes a few ticks to reach the drone.
DESCENT_LEAD_M = 0.05
DESCENT_TIMEOUT_SEC = 30.0
SETTLE_SEC = 5.0  # after the descent, before the hold's start point
BAG_TOPICS = [
    "/gesture/raw",
    "/gesture/intent",
    "/mission/velocity_target",
    "/mission/state",
    "/control/attitude_setpoint",
    "/estimate/velocity",
    "/flight/state",
    "/fmu/in/vehicle_attitude_setpoint_v1",
    "/fmu/in/offboard_control_mode",
    "/fmu/out/vehicle_local_position_v1",
    "/fmu/out/vehicle_status_v1",
]


class Recorder(Node):
    """Publishes the current gesture at 30 Hz; keeps the truth, PX4's
    estimate, the controller's setpoints and the mission and flight states.
    Times are wall-clock seconds (time.time()), which every container shares."""

    def __init__(self):
        super().__init__("sim_hold_scenario")
        self.gesture = NO_HANDS
        self.lock = threading.Lock()
        self.truth = []  # (t, x, y, z) Gazebo world, z up
        self.estimate = []  # (t, north, east, up) PX4's local position
        self.setpoints = []  # (t, /control/attitude_setpoint dict)
        self.mission = {}
        self.flight = {}

        best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self._raw = self.create_publisher(String, "/gesture/raw", 10)
        self.create_subscription(String, "/mission/state", self._on_mission, 10)
        self.create_subscription(String, "/flight/state", self._on_flight, 10)
        self.create_subscription(
            String, "/control/attitude_setpoint", self._on_setpoint, 10
        )
        self.create_subscription(
            VehicleLocalPosition,
            "/fmu/out/vehicle_local_position_v1",
            self._on_position,
            best_effort,
        )
        self.create_timer(1.0 / GESTURE_HZ, self._publish_gesture)

    def _publish_gesture(self):
        self._raw.publish(String(data=json.dumps({"gesture": self.gesture})))

    @staticmethod
    def _json(msg):
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict):
            return data
        return {}

    def _on_mission(self, msg):
        self.mission = self._json(msg)

    def _on_flight(self, msg):
        self.flight = self._json(msg)

    def _on_setpoint(self, msg):
        with self.lock:
            self.setpoints.append((time.time(), self._json(msg)))

    def _on_position(self, msg):
        # NED -> north, east, up
        with self.lock:
            self.estimate.append((time.time(), msg.x, msg.y, -msg.z))

    def on_truth(self, msg):
        """gz-transport callback (its own thread): the model's world pose."""
        for pose in msg.pose:
            if pose.name == MODEL:
                p = pose.position
                with self.lock:
                    self.truth.append((time.time(), p.x, p.y, p.z))
                return

    def height(self):
        """The latest true height (m), or None before the first pose."""
        with self.lock:
            if not self.truth:
                return None
            return self.truth[-1][3]

    def between(self, samples, t0, t1):
        with self.lock:
            return [s for s in samples if t0 <= s[0] <= t1]


def say(text):
    print(f"[sim-hold] {text}", flush=True)


def wait_for(what, condition, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    say(f"timed out after {timeout:.0f} s waiting for {what}")
    return False


def run_scenario(node, hold_sec):
    """The scripted descent and hold. Returns (checks, notes, window)."""
    notes = []
    if not wait_for("Gazebo ground truth", lambda: node.height() is not None, 30.0):
        return (
            [sc.Check("ground truth", False, f"nothing on {TRUTH_TOPIC}")],
            notes,
            None,
        )
    ready = wait_for(
        "mission_node WAITING (PX4 armed, in Offboard) with no hands seen",
        lambda: node.mission.get("mode") == "WAITING"
        and node.mission.get("reason") in ("no_hand", "no_hand_long"),
        READY_TIMEOUT_SEC,
    )
    if not ready:
        return (
            [sc.Check("chain ready", False, f"mission {node.mission or 'none'}")],
            notes,
            None,
        )
    mask = node.flight.get("mask")
    if mask != 15:
        # D4: the hold test is the ground owning every axis
        return (
            [sc.Check("mask", False, f"mask {mask}: the hold test needs 15")],
            notes,
            None,
        )
    notes.append(f"height when ready: {node.height():.2f} m (Gazebo)")
    say(f"chain ready, height {node.height():.2f} m")

    node.gesture = NO_HANDS
    time.sleep(1.0)
    node.gesture = ENGAGE
    time.sleep(1.5)
    node.gesture = NO_HANDS
    time.sleep(1.0)
    if node.mission.get("mode") != "ENGAGED":
        return (
            [sc.Check("ENGAGE", False, f"mode {node.mission.get('mode')}")],
            notes,
            None,
        )

    stop_at = hc.TARGET_HEIGHT_M + DESCENT_LEAD_M
    t_descent = time.time()
    node.gesture = DESCEND
    reached = wait_for(
        f"height <= {stop_at:.2f} m",
        lambda: node.height() <= stop_at,
        DESCENT_TIMEOUT_SEC,
    )
    node.gesture = NO_HANDS
    notes.append(
        f"descent: {time.time() - t_descent:.1f} s, stopped the gesture at "
        f"{node.height():.2f} m"
    )
    if not reached:
        return (
            [sc.Check("descent to 1.0 m", False, f"at {node.height():.2f} m")],
            notes,
            None,
        )

    time.sleep(SETTLE_SEC)
    t_start = time.time()
    say(f"hold: {hold_sec:.0f} s of zero velocity from {node.height():.2f} m")
    time.sleep(hold_sec + 0.5)
    t_end = time.time()

    truth = node.between(node.truth, t_start, t_end)
    checks = hc.hold_checks(truth, hold_sec)
    setpoints = [sp for _, sp in node.between(node.setpoints, t_start, t_end)]
    checks.append(hc.active_check(setpoints))

    estimate = node.between(node.estimate, t_start, t_end)
    if truth and estimate:
        true_h, true_v = hc.displacement(truth)
        est_h, est_v = hc.displacement(estimate)
        notes.append(
            f"start to end of the hold: truth {true_h:.3f} m horizontal, "
            f"{true_v:+.3f} m vertical; PX4 estimate {est_h:.3f} m, {est_v:+.3f} m"
        )
    return checks, notes, (t_start, t_end)


def write_truth(node, path):
    with node.lock:
        rows = list(node.truth)
    with open(path, "w") as f:
        f.write("t,x,y,z\n")
        for t, x, y, z in rows:
            f.write(f"{t:.3f},{x:.4f},{y:.4f},{z:.4f}\n")


def run(argv=None):
    """The whole test; returns the exit code (main() exits with it)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    default_out = os.path.join(os.environ.get("LOG_DIR", "/tmp"), "sim-hold", stamp)
    parser.add_argument("--out", default=default_out)
    parser.add_argument("--hold-sec", type=float, default=hc.HOLD_SEC)
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    rclpy.init()
    node = Recorder()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    gz = GzNode()
    if not gz.subscribe(Pose_V, TRUTH_TOPIC, node.on_truth):
        say(f"could not subscribe to {TRUTH_TOPIC}")

    intent_exe = os.path.join(
        get_package_prefix("intent_node"), "lib", "intent_node", "intent_node"
    )
    with open(os.path.join(args.out, "intent_node.log"), "w") as intent_log:
        intent = subprocess.Popen(
            [intent_exe, "--ros-args", "-p", "input_source:=topic"],
            stdout=intent_log,
            stderr=subprocess.STDOUT,
        )
        bag = subprocess.Popen(
            ["ros2", "bag", "record", "-o", os.path.join(args.out, "bag"), *BAG_TOPICS],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            checks, notes, window = run_scenario(node, args.hold_sec)
        finally:
            node.gesture = NO_HANDS
            for proc in (intent, bag):
                if proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()

    write_truth(node, os.path.join(args.out, "truth.csv"))
    text = sc.summary(checks, title=f"make sim-hold {stamp} (REQ-NAV-001)")
    if window is not None:
        notes.append(f"hold window (wall clock): {window[0]:.3f} to {window[1]:.3f}")
    if notes:
        text += "\n\nMeasured, not judged:\n" + "\n".join(f"- {n}" for n in notes)
    with open(os.path.join(args.out, "summary.md"), "w") as f:
        f.write(text + "\n")
    print("\n" + text, flush=True)
    say(f"rosbag, truth.csv and summary: {args.out}")

    # Stop the spin thread before anything is destroyed (as smoke_scenario).
    gz.unsubscribe(TRUTH_TOPIC)
    rclpy.try_shutdown()
    executor.shutdown()
    spinner.join(timeout=5)
    node.destroy_node()
    if checks and all(c.ok for c in checks):
        return 0
    return 1


def main(argv=None):
    """Entry point (sim_hold_scenario). Exits without the interpreter's own
    shutdown: it destroys gz-transport's C++ threads while they still run,
    which aborts the process ("terminate called without an active
    exception", exit code 134) and would turn a pass into a failure.
    Everything is written and flushed before."""
    code = run(argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
