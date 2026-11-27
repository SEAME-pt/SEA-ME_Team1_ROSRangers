#!/usr/bin/env python3
"""sim_smoke_scenario: scripted gestures against PX4 SITL, then the checks (SIM ONLY, #86, #199).

make sim-smoke runs it in its own container once the px4 container is healthy
(PX4 armed, airborne and in Offboard, flight_interface streaming the
controller's attitude setpoints through its px4 backend). It starts its own
intent_node, so it can kill it for the REQ-SAF-017 check, and drives the chain
through /gesture/raw only (mission_node owns /mission/velocity_target):

    no hands -> ENGAGE -> MOVE_RIGHT 4 s -> CLIMB_UP 3 s
             -> MOVE_RIGHT, then intent_node killed (SIGKILL) -> no hands

It records a rosbag of the chain, judges what happened with smoke_checks and
writes summary.md next to the bag. Exit code 0 only if every check passed.
The mask comes from /flight/state (flight_interface's, from config/sim.yaml),
the hover throttle from the same file (mounted at /config, SIM_CONFIG
replaces the path).

    sim_smoke_scenario [--out DIR]     (default: $LOG_DIR/sim-smoke/<start time>)
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
import yaml
from ament_index_python.packages import get_package_prefix
from geometry_msgs.msg import TwistStamped
from px4_msgs.msg import VehicleLocalPosition
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from sim_smoke import smoke_checks as sc

GESTURE_HZ = 30.0  # about gesture_node's camera rate
NO_HANDS = "NONE"
ENGAGE = "L:OPEN_HAND_UP+R:OPEN_HAND_UP"
MOVE_RIGHT = "L:NONE+R:THUMB_RIGHT"
CLIMB_UP = "L:NONE+R:INDEX_UP"

READY_TIMEOUT_SEC = 60.0
SIM_CONFIG = os.environ.get("SIM_CONFIG", "/config/sim.yaml")
BAG_TOPICS = [
    "/gesture/raw",
    "/gesture/intent",
    "/mission/velocity_target",
    "/mission/state",
    "/control/attitude_setpoint",
    "/estimate/velocity",
    "/flight/state",
    "/fmu/in/vehicle_attitude_setpoint_v1",
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
        # Times in seconds on the wall clock, which every container shares.
        # Targets and setpoints carry their PUBLISH stamp, so the DDS delay to
        # this container is not counted against mission_node (REQ-SAF-017).
        self.intent_times = []  # receive time
        self.targets = []  # (header.stamp, (vx, vy, vz, wz))
        self.setpoints = []  # (receive time, /control/attitude_setpoint dict)
        self.velocities = []  # (receive time, (v_north, v_east, v_down)) measured
        self.positions = []  # (receive time, (north, east, down)) measured
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
            String, "/control/attitude_setpoint", self._on_setpoint, 10
        )
        self.create_subscription(
            VehicleLocalPosition,
            "/fmu/out/vehicle_local_position_v1",
            self._on_position,
            best_effort,
        )
        self.create_timer(1.0 / GESTURE_HZ, self._publish_gesture)

    def now(self):
        """Wall clock (s), the clock mission_node stamps with."""
        return self.get_clock().now().nanoseconds / 1e9

    def _publish_gesture(self):
        self._raw.publish(String(data=json.dumps({"gesture": self.gesture})))

    def _on_intent(self, _msg):
        self.intent_times.append(self.now())

    def _on_target(self, msg):
        t = msg.twist
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
        self.targets.append((stamp, (t.linear.x, t.linear.y, t.linear.z, t.angular.z)))

    def _on_setpoint(self, msg):
        self.setpoints.append((self.now(), self._json(msg)))

    def _on_position(self, msg):
        self.position = (msg.x, msg.y, msg.z, msg.heading)
        now = self.now()
        self.velocities.append((now, (msg.vx, msg.vy, msg.vz)))
        self.positions.append((now, (msg.x, msg.y, msg.z)))

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


def hover_throttle(path=SIM_CONFIG):
    """config/sim.yaml's hover throttle (ADR-025 F20), the controller's."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return float(data["controller_node"]["ros__parameters"]["hover_throttle"])


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
    n1, e1, d1, yaw1 = node.position
    displacement = sc.body_displacement((n0, e0, d0), (n1, e1, d1), yaw)
    turned = sc.heading_change_deg(yaw, yaw1)
    check = sc.movement_check(name, displacement, axis, sign, mask, turned)
    say(f"{name}: {check.detail}")
    return check


def window(samples, t0):
    return [values for t, values in samples if t >= t0]


# REQ-SAF-017: the hover is judged from 2.5 s after the kill (the controller
# has stopped the move by then) for HOVER_WINDOW_SEC.
HOVER_FROM_SEC = 2.5
HOVER_WINDOW_SEC = 4.0


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
    mask = node.flight.get("mask")
    if mask is None:
        # Never assume a mask: 15 would judge REQ-SAF-018 as if the ground
        # owned every axis (hold_scenario does the same)
        return [sc.Check("mask", False, "no mask in /flight/state")]
    if mask not in sc.SMOKE_MASKS:
        # Only right and up are flown: under mask 8 nothing moves, so the
        # REQ-SAF-017 check could never pass. config/sim.yaml sets the mask.
        return [sc.Check("mask", False, f"mask {mask}: sim-smoke needs 11 or 15")]
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
    # NO_HANDS until the hover window (from HOVER_FROM_SEC after the kill) is over
    hold(node, NO_HANDS, HOVER_FROM_SEC + HOVER_WINDOW_SEC - 1.5)
    last_intent = max(node.intent_times, default=None)
    checks.append(sc.loss_check(last_intent, node.targets))
    hover_from = t_kill + HOVER_FROM_SEC
    checks.append(sc.hover_check(window(node.velocities, hover_from)))
    checks.append(sc.hover_drift_check(window(node.positions, hover_from)))

    targets = window(node.targets, t_start)
    checks.append(sc.rate_check([t for t, _ in node.targets if t >= t_start]))
    checks.append(sc.limits_check(targets))
    checks.append(sc.measured_speed_check(window(node.velocities, t_start)))
    checks.append(sc.mask_targets_check(targets, mask))
    checks.append(
        sc.mask_attitude_check(window(node.setpoints, t_start), mask, hover_throttle())
    )
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
