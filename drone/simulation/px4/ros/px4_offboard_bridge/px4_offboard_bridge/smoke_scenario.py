#!/usr/bin/env python3
"""sim_smoke_scenario: scripted gestures against PX4 SITL, then the checks (SIM ONLY, #86).

make sim-smoke runs it in its own container once the px4 container is healthy
(PX4 armed, airborne and in Offboard, the bridge streaming). It starts its own
intent_node, so it can kill it for the REQ-SAF-017 check, and drives the chain
through /gesture/raw only (mission_node owns /mission/velocity_target):

    no hands -> ENGAGE -> MOVE_RIGHT 4 s -> CLIMB_UP 3 s
             -> MOVE_RIGHT, then intent_node killed (SIGKILL) -> no hands

It records a rosbag of the chain, judges what happened with smoke_checks and
writes summary.md next to the bag. Exit code 0 only if every check passed.

    sim_smoke_scenario [--out DIR]     (default: $LOG_DIR/sim-smoke/<start time>)
"""

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

import rclpy
from ament_index_python.packages import get_package_prefix
from geometry_msgs.msg import TwistStamped
from px4_msgs.msg import TrajectorySetpoint, VehicleLocalPosition
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from px4_offboard_bridge import smoke_checks as sc

GESTURE_HZ = 30.0  # about gesture_node's camera rate
NO_HANDS = "NONE"
ENGAGE = "L:OPEN_HAND_UP+R:OPEN_HAND_UP"
MOVE_RIGHT = "L:NONE+R:THUMB_RIGHT"
CLIMB_UP = "L:NONE+R:INDEX_UP"

READY_TIMEOUT_SEC = 60.0
BAG_TOPICS = [
    "/gesture/raw",
    "/gesture/intent",
    "/mission/velocity_target",
    "/mission/state",
    "/flight/state",
    "/fmu/in/trajectory_setpoint",
    "/fmu/in/offboard_control_mode",
    "/fmu/out/vehicle_local_position_v1",
    "/fmu/out/vehicle_status_v1",
    "/fmu/out/vehicle_land_detected",
]


class Recorder(Node):
    """Publishes the current gesture at 30 Hz and keeps what the chain sends."""

    def __init__(self):
        super().__init__("sim_smoke_scenario")
        self.gesture = NO_HANDS
        self.intent_times = []
        self.targets = []  # (t, (vx, vy, vz, wz))
        self.setpoints = []  # (t, (v_north, v_east, v_down, yawspeed))
        self.velocities = []  # (t, (v_north, v_east, v_down)) measured
        self.position = None  # (n, e, d, heading)
        self.mission = {}
        self.flight = {}

        best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self._raw = self.create_publisher(String, "/gesture/raw", 10)
        self.create_subscription(String, "/gesture/intent", self._on_intent, 10)
        self.create_subscription(
            TwistStamped, "/mission/velocity_target", self._on_target, 10
        )
        self.create_subscription(String, "/mission/state", self._on_mission, 10)
        self.create_subscription(String, "/flight/state", self._on_flight, 10)
        self.create_subscription(
            TrajectorySetpoint,
            "/fmu/in/trajectory_setpoint",
            self._on_setpoint,
            best_effort,
        )
        self.create_subscription(
            VehicleLocalPosition,
            "/fmu/out/vehicle_local_position_v1",
            self._on_position,
            best_effort,
        )
        self.create_timer(1.0 / GESTURE_HZ, self._publish_gesture)

    @staticmethod
    def now():
        return time.monotonic()

    def _publish_gesture(self):
        self._raw.publish(String(data=json.dumps({"gesture": self.gesture})))

    def _on_intent(self, _msg):
        self.intent_times.append(self.now())

    def _on_target(self, msg):
        t = msg.twist
        self.targets.append(
            (self.now(), (t.linear.x, t.linear.y, t.linear.z, t.angular.z))
        )

    def _on_setpoint(self, msg):
        v = msg.velocity
        self.setpoints.append((self.now(), (v[0], v[1], v[2], msg.yawspeed)))

    def _on_position(self, msg):
        self.position = (msg.x, msg.y, msg.z, msg.heading)
        self.velocities.append((self.now(), (msg.vx, msg.vy, msg.vz)))

    @staticmethod
    def _json(msg):
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _on_mission(self, msg):
        self.mission = self._json(msg)

    def _on_flight(self, msg):
        self.flight = self._json(msg)


def say(text):
    print(f"[sim-smoke] {text}", flush=True)


def wait_for(what, condition, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    say(f"timed out after {timeout:.0f} s waiting for {what}")
    return False


def hold(node, gesture, seconds):
    node.gesture = gesture
    time.sleep(seconds)


def move(node, name, gesture, seconds, axis, sign, mask):
    """Hold a gesture, stop, settle, and check where the drone went."""
    if node.position is None:
        return sc.Check(name, False, "no PX4 position")
    n0, e0, d0, yaw = node.position
    hold(node, gesture, seconds)
    hold(node, NO_HANDS, 2.0)
    n1, e1, d1, _ = node.position
    displacement = sc.body_displacement((n0, e0, d0), (n1, e1, d1), yaw)
    check = sc.movement_check(name, displacement, axis, sign, mask)
    say(f"{name}: {check.detail}")
    return check


def window(samples, t0):
    return [values for t, values in samples if t >= t0]


def run_scenario(node, intent):
    """The scripted flight. Returns the list of checks."""
    checks = []
    # ENGAGE counts only when it STARTS after a real "no hands" reading
    # (mission_node's HAND_REASONS): "no_packets_yet" while our intent_node is
    # still starting is not one, so wait for no_hand (no_hand_long after 2 s)
    # before showing it.
    ready = wait_for(
        "mission_node WAITING (PX4 armed, in Offboard) with no hands seen",
        lambda: node.mission.get("mode") == "WAITING"
        and node.mission.get("reason") in ("no_hand", "no_hand_long"),
        READY_TIMEOUT_SEC,
    )
    if not ready:
        return [
            sc.Check("chain ready", False, f"mission state {node.mission or 'none'}")
        ]
    mask = int(node.flight.get("mask", 15))
    say(f"chain ready, mask {mask}")

    t_start = node.now()
    hold(node, NO_HANDS, 1.0)
    hold(node, ENGAGE, 1.5)
    engaged = node.mission.get("mode") == "ENGAGED"
    checks.append(sc.Check("ENGAGE", engaged, f"mode {node.mission.get('mode')}"))
    hold(node, NO_HANDS, 1.0)
    if not engaged:
        return checks

    checks.append(
        move(node, "lateral: MOVE_RIGHT 4 s", MOVE_RIGHT, 4.0, "left", -1, mask)
    )
    checks.append(move(node, "vertical: CLIMB_UP 3 s", CLIMB_UP, 3.0, "up", +1, mask))

    say("intent loss: MOVE_RIGHT, then intent_node killed")
    hold(node, MOVE_RIGHT, 2.0)
    t_kill = node.now()
    intent.send_signal(signal.SIGKILL)
    intent.wait()
    hold(node, MOVE_RIGHT, 1.5)  # gestures keep coming: only /gesture/intent is lost
    hold(node, NO_HANDS, 2.0)
    last_intent = max(node.intent_times, default=None)
    checks += sc.loss_checks(last_intent, node.targets, node.setpoints)
    stopped = [v for t, v in node.velocities if t >= t_kill + 2.5]
    still = bool(stopped) and max(math.hypot(v[0], v[1]) for v in stopped) <= 0.1
    checks.append(
        sc.Check(
            "hovers after the loss",
            still,
            "horizontal speed <= 0.1 m/s 2.5 s after the kill",
        )
    )

    targets = window(node.targets, t_start)
    checks.append(sc.rate_check([t for t, _ in node.targets if t >= t_start]))
    checks.append(sc.limits_check(targets))
    checks.append(sc.measured_speed_check(window(node.velocities, t_start)))
    checks.append(sc.mask_targets_check(targets, mask))
    checks.append(sc.mask_setpoints_check(window(node.setpoints, t_start), mask))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    default_out = os.path.join(os.environ.get("LOG_DIR", "/tmp"), "sim-smoke", stamp)
    parser.add_argument("--out", default=default_out)
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    rclpy.init()
    node = Recorder()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()

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
            checks = run_scenario(node, intent)
        finally:
            node.gesture = NO_HANDS
            for proc in (intent, bag):
                if proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()

    text = sc.summary(checks, title=f"make sim-smoke {stamp}")
    with open(os.path.join(args.out, "summary.md"), "w") as f:
        f.write(text + "\n")
    print("\n" + text, flush=True)
    say(f"rosbag and summary: {args.out}")

    # Stop the spin thread before anything is destroyed: a thread still inside
    # the executor at exit aborts the process (std::terminate, exit code 134).
    rclpy.try_shutdown()
    executor.shutdown()
    spinner.join(timeout=5)
    node.destroy_node()
    return 0 if checks and all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
