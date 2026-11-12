#!/usr/bin/env python3
"""flight_interface lifecycle node (#103): /mission/velocity_target -> drone,
drone -> /flight/state, with a watchdog and an E-stop.

Lifecycle (ros2 lifecycle set /flight_interface <transition>):
  * unconfigured: nothing runs, nothing is published. mission_node sees no
    /flight/state and stays IDLE.
  * configure: opens the backend, starts the watchdog and /flight/state
    (link_ok false until active).
  * activate: setpoints go out at rate_hz, only from a target that arrives
    after the activation.
  * deactivate: one last zero setpoint, then nothing more.
  * cleanup / shutdown: closes the backend.

E-stop (decision A1): service ~/estop (std_srvs/Trigger) sends one neutral
setpoint (mode 0) and then BYE, so the ESP ends the session and starts its
ADR-004 output at once. Nothing more is sent. It is latched on the node:
deactivate, cleanup and configure keep it, only ~/reset_estop clears it, and
afterwards control needs a new session (the ESP backend opens one by itself,
decision A5), an operator ARM and a NEW target. A process restart starts
without the latch (decision A6): the launch file has no respawn, and after a
restart nothing is sent before configure, activate and an operator ARM.

Link loss (decision A2): zero setpoints and link_ok false, no latch. The way
back after the ESP's link failsafe is the operator's ~/resume (decision A3):
it is refused unless the node is active, not in E-stop, the link is fresh and
the ESP reports LINK_FAILSAFE; then RESUME_REQUEST goes out after 0.6 s of
neutral setpoints. The node never resumes by itself.

ARM (decision A4): only the operator's ~/arm_control sends ARM_CONTROL_REQUEST,
with the `mask` parameter (8, 11 or 15, checked at configure). It is refused
unless the node is active, not in E-stop, the link is fresh, the ESP reports
SESSION_IDLE and the FC link is not reported lost. The node never sends ARM on
its own, also not after a reconnect, a session switch or a reset
(REQ-SAF-023).

The ESP's answer to ARM and RESUME (decision E2): every ARM_CONTROL_ACK and
RESUME_ACK is logged, a refusal as a warning with its reason (e.g.
MASK_MISMATCH: the ground must not fly). The services answer when the request
is sent, so this log is where the operator sees the ESP's verdict.

FC telemetry (decision D2): with publish_fc true, /fc/attitude
(Vector3Stamped, rad, REP-103) and /fc/battery (BatteryState) come from the FC
values in every STATE, through the same conversion as fc_telemetry
(telemetry.py), with the yaw zeroed at the first sample after each activate.
Only while the ESP reports the FC link alive (fc_link_ok true): STATE carries
no age for them, so they are zero before the FC's first reply and frozen for
up to 10 s after it goes silent. Do not run fc_telemetry on the same graph.

Freshness uses time.monotonic(). Logic: guard.py (no ROS). Backends: backends.py.
"""

import json
import math
import signal
import sys
import threading
import time

import rclpy
from geometry_msgs.msg import TwistStamped, Vector3Stamped
from rclpy.exceptions import ParameterException
from rclpy.executors import SingleThreadedExecutor
from rclpy.lifecycle import Node, State, TransitionCallbackReturn
from rclpy.logging import get_logger
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String
from std_srvs.srv import Trigger

from flight_interface.backends import make_backend
from flight_interface.guard import LINK_TIMEOUT, MAX_TARGET_TIMEOUT, ZERO, Guard
from flight_interface.telemetry import (
    AttitudeConverter,
    battery_fields,
    state_analog,
    state_attitude,
)

NODE_NAME = "flight_interface"
MIN_RATE_HZ, MAX_RATE_HZ = 10.0, 100.0
# mission_node calls a /flight/state older than 0.5 s lost: publish well above 2 Hz.
MIN_STATE_RATE_HZ = 5.0
ATTITUDE_TOPIC = "/fc/attitude"  # the same topics as fc_telemetry (D2)
BATTERY_TOPIC = "/fc/battery"
# Why the ESP refused ARM or RESUME (Protocol Spec v0 §3.1), for the log (E2).
ACK_REASONS = {
    "WRONG_STATE": "the ESP is not in the state this request needs",
    "MASK_MISMATCH": "the ESP runs another override mask: the ground must not fly",
    "RESUME_CONDITION_NOT_MET": "no unbroken neutral stream for 500 ms",
    "FC_NOT_ANSWERING": "the FC does not answer the ESP",
}


class FlightInterface(Node):
    def __init__(self, backend=None):
        """`backend` replaces the one named by the parameter (tests)."""
        super().__init__(NODE_NAME)
        self.declare_parameter("backend", "loopback")
        self.declare_parameter("rate_hz", 50.0)  # SETPOINT stream (Protocol Spec v0 §3)
        self.declare_parameter("state_rate_hz", 10.0)  # /flight/state
        self.declare_parameter("target_timeout_sec", MAX_TARGET_TIMEOUT)
        self.declare_parameter("link_timeout_sec", LINK_TIMEOUT)
        self.declare_parameter("velocity_topic", "/mission/velocity_target")
        self.declare_parameter("state_topic", "/flight/state")
        # backend:=esp only. The PSK is never a parameter: SEAME_PSK or
        # ~/.config/seame-drone/secrets.json (esp_backend.load_psk).
        self.declare_parameter("mask", 8)  # ARM mask (ADR-010: 8 yaw, 11, 15)
        self.declare_parameter("esp_host", "192.168.4.1")
        self.declare_parameter("esp_port", 5759)
        # D2: /fc/attitude and /fc/battery from STATE (backend:=esp). False
        # when fc_telemetry publishes them instead.
        self.declare_parameter("publish_fc", True)
        self.declare_parameter("frame_id", "base_link")  # as fc_telemetry
        self.declare_parameter("cell_count", 4)  # CNHL 4S pack, as fc_telemetry
        self._injected_backend = backend
        self._mask = 8
        self.backend = None
        self.guard = None
        self._entities = []
        self._link_was_fresh = False
        self._estop_reason = ""  # the latch lives here, so cleanup cannot clear it
        self._attitude = None  # AttitudeConverter while /fc/* are published
        self.get_logger().info(
            "unconfigured: use ros2 lifecycle set /flight_interface configure"
        )

    # ---------- lifecycle ----------
    def on_configure(self, state: State) -> TransitionCallbackReturn:
        try:
            rate = float(self.get_parameter("rate_hz").value)
            state_rate = float(self.get_parameter("state_rate_hz").value)
            target_timeout = float(self.get_parameter("target_timeout_sec").value)
            link_timeout = float(self.get_parameter("link_timeout_sec").value)
            mask = self.get_parameter("mask").value
            if isinstance(mask, bool) or mask not in (8, 11, 15):
                raise ValueError(f"mask must be 8, 11 or 15, got {mask!r}")
            # Types: ROS refuses a value of another type for a declared parameter.
            publish_fc = self.get_parameter("publish_fc").value
            cell_count = self.get_parameter("cell_count").value
            if cell_count < 1:
                raise ValueError(f"cell_count must be 1 or more, got {cell_count}")
            backend = self._injected_backend or make_backend(
                str(self.get_parameter("backend").value),
                esp_host=str(self.get_parameter("esp_host").value),
                esp_port=int(self.get_parameter("esp_port").value),
                warn=self.get_logger().warning,  # E3: a secrets file others can read
            )
        except (TypeError, ValueError, ImportError) as error:
            # ImportError: backend:=esp without sd_protocol installed.
            self.get_logger().error(f"configure failed: {error}")
            return TransitionCallbackReturn.FAILURE
        rate = min(MAX_RATE_HZ, max(MIN_RATE_HZ, rate))
        state_rate = min(rate, max(MIN_STATE_RATE_HZ, state_rate))
        self.backend = backend
        self._mask = mask
        self._frame_id = str(self.get_parameter("frame_id").value)
        self._cell_count = cell_count
        self.guard = Guard(target_timeout, link_timeout, tick_period=1.0 / rate)
        if self._estop_reason:
            self.guard.trigger_estop(self._estop_reason)  # still latched
        self._link_was_fresh = False
        # (destroy function, entity), undone in _release(). Each entity is
        # added as soon as it exists, so a failure half-way releases what was
        # already created (on_error -> _release).
        self._entities = []

        def keep(destroy, entity):
            self._entities.append((destroy, entity))
            return entity

        self._state_pub = keep(
            self.destroy_publisher,
            self.create_publisher(
                String, str(self.get_parameter("state_topic").value), 10
            ),
        )
        keep(
            self.destroy_subscription,
            self.create_subscription(
                TwistStamped,
                str(self.get_parameter("velocity_topic").value),
                self._on_target,
                10,
            ),
        )
        if publish_fc:
            self._attitude = AttitudeConverter()
            self._attitude_pub = keep(
                self.destroy_publisher,
                self.create_publisher(Vector3Stamped, ATTITUDE_TOPIC, 10),
            )
            self._battery_pub = keep(
                self.destroy_publisher,
                self.create_publisher(BatteryState, BATTERY_TOPIC, 10),
            )
        keep(self.destroy_timer, self.create_timer(1.0 / rate, self._tick))
        keep(
            self.destroy_timer, self.create_timer(1.0 / state_rate, self._publish_state)
        )
        keep(
            self.destroy_service,
            self.create_service(Trigger, "~/estop", self._on_estop),
        )
        keep(
            self.destroy_service,
            self.create_service(Trigger, "~/reset_estop", self._on_reset_estop),
        )
        keep(
            self.destroy_service,
            self.create_service(Trigger, "~/resume", self._on_resume),
        )
        keep(
            self.destroy_service,
            self.create_service(Trigger, "~/arm_control", self._on_arm_control),
        )
        self.get_logger().info(
            f"configured: backend {backend.name}, {rate:.0f} Hz, zero after "
            f"{self.guard.target_timeout:.2f} s, link lost after {self.guard.link_timeout:.2f} s"
        )
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        self.guard.activate()
        if self._attitude is not None:
            self._attitude.reset_yaw()  # D2: yaw 0 at the first sample from now
        self.get_logger().info("active: setpoints go out from the next fresh target")
        return super().on_activate(state)

    def on_deactivate(self, state: State) -> TransitionCallbackReturn:
        was_sending = self.guard.active and not self.guard.estop
        self.guard.deactivate()
        if was_sending:
            self.backend.send_setpoint(ZERO, time.monotonic())  # one last zero
        self._publish_state()
        self.get_logger().info("inactive: no more setpoints")
        return super().on_deactivate(state)

    def on_cleanup(self, state: State) -> TransitionCallbackReturn:
        self._release()
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: State) -> TransitionCallbackReturn:
        if self.guard is not None and self.guard.active:
            self.on_deactivate(state)
        self._release()
        return TransitionCallbackReturn.SUCCESS

    def on_error(self, state: State) -> TransitionCallbackReturn:
        """A transition raised (a backend error, for example): free everything,
        so the node lands in unconfigured with nothing left running."""
        self.get_logger().error(
            f"transition error in {state.label}: releasing everything"
        )
        try:
            self._release()
        except Exception as error:  # the backend may fail again
            self.get_logger().error(f"release failed: {error}")
            self._entities, self.backend, self.guard = [], None, None
        return TransitionCallbackReturn.SUCCESS

    def _release(self):
        for destroy, entity in self._entities:
            destroy(entity)
        self._entities = []
        if self.backend is not None:
            self.backend.close()
        self.backend = None
        self.guard = None
        self._attitude = None

    # ---------- callbacks ----------
    def _on_target(self, msg):
        t = msg.twist
        self.guard.on_target(
            t.linear.x, t.linear.y, t.linear.z, t.angular.z, time.monotonic()
        )

    def _tick(self):
        now = time.monotonic()
        for drone_state in self.backend.poll(now):
            # The backend's receive time when it gives one (never in the future).
            rx = drone_state.get("rx_time") if isinstance(drone_state, dict) else None
            valid_rx = isinstance(rx, (int, float)) and math.isfinite(rx) and rx <= now
            self.guard.on_drone_state(drone_state, rx if valid_rx else now)
            self._publish_fc(drone_state)
        for kind, code, reason in self.backend.take_acks():
            self._log_ack(kind, code, reason)
        fresh = self.guard.link_fresh(now)
        if fresh != self._link_was_fresh:
            # One call site per severity: rclpy refuses to change it between calls.
            if fresh:
                self.get_logger().info("drone link up")
            else:
                self.get_logger().warning(
                    "drone link LOST: zero setpoints, mission_node goes IDLE"
                )
            self._link_was_fresh = fresh
        command = self.guard.command(now)
        if command is not None:
            self.backend.send_setpoint(command, now)
            self.guard.note_sent(command, now)
        if self.guard.resume_due(now):
            if self.backend.request_resume(now):
                self.get_logger().warning("RESUME_REQUEST sent (operator request)")
            else:
                self.get_logger().error(
                    "RESUME_REQUEST not sent: no TCP to the ESP; ask again"
                )

    def _publish_fc(self, drone_state):
        """D2: /fc/attitude and /fc/battery from a STATE's FC values, only while
        the ESP reports the FC link alive (see the module docstring)."""
        if self._attitude is None or not isinstance(drone_state, dict):
            return
        values = drone_state.get("fc_values")
        if not isinstance(values, dict) or drone_state.get("fc_link_ok") is not True:
            return
        stamp = self.get_clock().now().to_msg()
        attitude = Vector3Stamped()
        attitude.header.stamp = stamp
        attitude.header.frame_id = self._frame_id
        x, y, z = self._attitude.convert(state_attitude(values))
        attitude.vector.x, attitude.vector.y, attitude.vector.z = x, y, z
        self._attitude_pub.publish(attitude)
        battery = BatteryState()
        battery.header.stamp = stamp
        battery.header.frame_id = self._frame_id
        for field, value in battery_fields(
            state_analog(values), self._cell_count
        ).items():
            setattr(battery, field, value)
        self._battery_pub.publish(battery)

    def _log_ack(self, kind, code, reason):
        """E2: the ESP's answer to ARM or RESUME, for the operator."""
        # One call site per severity: rclpy refuses to change it between calls.
        if code == 0:
            self.get_logger().info(f"{kind}: OK")
            return
        why = ACK_REASONS.get(reason)
        detail = f": {why}" if why else ""
        self.get_logger().warning(
            f"{kind} refused by the ESP, {reason} ({code}){detail}"
        )

    def _publish_state(self):
        state = self.guard.flight_state(time.monotonic())
        self._state_pub.publish(String(data=json.dumps(state, separators=(",", ":"))))

    def _on_estop(self, request, response):
        """A1: latch first, then one neutral (if setpoints were going out) and
        BYE. A backend error cannot undo the latch."""
        was_sending = self.guard.active and not self.guard.estop
        self._estop_reason = "service"
        self.guard.trigger_estop(self._estop_reason)
        now = time.monotonic()
        try:
            if was_sending:
                self.backend.send_neutral(now)  # one neutral, then nothing
            self.backend.end_session(now)  # BYE: the ESP's failsafe output at once
        except Exception as error:  # the latch holds whatever the backend does
            self.get_logger().error(f"E-STOP: backend error after the latch: {error}")
        self._publish_state()
        self.get_logger().error(
            "E-STOP: neutral + BYE sent, setpoints stopped; ~/reset_estop to clear"
        )
        response.success = True
        response.message = "estop latched"
        return response

    def _on_arm_control(self, request, response):
        """A4: the operator's ARM. The ONLY place that calls arm_control()."""
        now = time.monotonic()
        ok, why = self.guard.arm_allowed(self._mask, now)
        if ok:
            ok = self.backend.arm_control(self._mask, now)
            why = f"{why} sent" if ok else f"{why} not sent: no TCP to the ESP"
        response.success, response.message = ok, why
        # One call site per severity: rclpy refuses to change it between calls.
        if ok:
            self.get_logger().warning(f"ARM (operator): {why}")
        else:
            self.get_logger().info(f"ARM refused: {why}")
        return response

    def _on_resume(self, request, response):
        """A3: the operator asks to resume after the ESP's link failsafe."""
        response.success, response.message = self.guard.request_resume(time.monotonic())
        # One call site per severity: rclpy refuses to change it between calls.
        if response.success:
            self.get_logger().warning(f"resume requested: {response.message}")
        else:
            self.get_logger().info(f"resume refused: {response.message}")
        return response

    def _on_reset_estop(self, request, response):
        response.success = self.guard.reset_estop()
        self._estop_reason = ""
        response.message = (
            "estop cleared: waiting for a new target"
            if response.success
            else "no estop"
        )
        if response.success:
            self.get_logger().warning(response.message)
        return response


def main(args=None):
    """Run until Ctrl+C or SIGTERM. The signals only stop the spin loop, so the
    node can still deactivate (one last zero setpoint) and close the backend."""
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stop.set())
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    try:
        node = FlightInterface()
    except (
        ValueError,
        ParameterException,
    ) as error:  # e.g. a parameter of the wrong type
        get_logger(NODE_NAME).fatal(str(error))
        rclpy.try_shutdown()
        sys.exit(1)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    try:
        while not stop.is_set():
            executor.spin_once(timeout_sec=0.1)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        if node.guard is not None:
            node.trigger_shutdown()
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
    sys.exit(0)


if __name__ == "__main__":
    main()
