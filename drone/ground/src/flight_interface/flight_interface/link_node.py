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

E-stop: service ~/estop (std_srvs/Trigger) sends one zero setpoint and then
nothing more, at once; the ESP then enters its own 250 ms link failsafe
(ADR-004). It is latched on the node: deactivate, cleanup and configure keep
it, only ~/reset_estop clears it, and a NEW target is needed afterwards. A
process restart starts without it.

The node never sends ARM_CONTROL_REQUEST (REQ-SAF-023): in this first part the
backends have no way to send it, and arming is always an operator action.

Freshness uses time.monotonic(). Logic: guard.py (no ROS). Backends: backends.py.
"""

import json
import signal
import sys
import threading
import time

import rclpy
from geometry_msgs.msg import TwistStamped
from rclpy.exceptions import ParameterException
from rclpy.executors import SingleThreadedExecutor
from rclpy.lifecycle import Node, State, TransitionCallbackReturn
from rclpy.logging import get_logger
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import String
from std_srvs.srv import Trigger

from flight_interface.backends import make_backend
from flight_interface.guard import LINK_TIMEOUT, MAX_TARGET_TIMEOUT, ZERO, Guard

NODE_NAME = "flight_interface"
MIN_RATE_HZ, MAX_RATE_HZ = 10.0, 100.0
# mission_node calls a /flight/state older than 0.5 s lost: publish well above 2 Hz.
MIN_STATE_RATE_HZ = 5.0


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
        self._injected_backend = backend
        self.backend = None
        self.guard = None
        self._entities = []
        self._link_was_fresh = False
        self._estop_reason = ""  # the latch lives here, so cleanup cannot clear it
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
            backend = self._injected_backend or make_backend(
                str(self.get_parameter("backend").value)
            )
        except (TypeError, ValueError) as error:
            self.get_logger().error(f"configure failed: {error}")
            return TransitionCallbackReturn.FAILURE
        rate = min(MAX_RATE_HZ, max(MIN_RATE_HZ, rate))
        state_rate = min(rate, max(MIN_STATE_RATE_HZ, state_rate))
        self.backend = backend
        self.guard = Guard(target_timeout, link_timeout, tick_period=1.0 / rate)
        if self._estop_reason:
            self.guard.trigger_estop(self._estop_reason)  # still latched
        self._link_was_fresh = False
        self._state_pub = self.create_publisher(
            String, str(self.get_parameter("state_topic").value), 10
        )
        # (destroy function, entity), undone in _release()
        self._entities = [
            (self.destroy_publisher, self._state_pub),
            (
                self.destroy_subscription,
                self.create_subscription(
                    TwistStamped,
                    str(self.get_parameter("velocity_topic").value),
                    self._on_target,
                    10,
                ),
            ),
            (self.destroy_timer, self.create_timer(1.0 / rate, self._tick)),
            (
                self.destroy_timer,
                self.create_timer(1.0 / state_rate, self._publish_state),
            ),
            (
                self.destroy_service,
                self.create_service(Trigger, "~/estop", self._on_estop),
            ),
            (
                self.destroy_service,
                self.create_service(Trigger, "~/reset_estop", self._on_reset_estop),
            ),
        ]
        self.get_logger().info(
            f"configured: backend {backend.name}, {rate:.0f} Hz, zero after "
            f"{self.guard.target_timeout:.2f} s, link lost after {self.guard.link_timeout:.2f} s"
        )
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        self.guard.activate()
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

    # ---------- callbacks ----------
    def _on_target(self, msg):
        t = msg.twist
        self.guard.on_target(
            t.linear.x, t.linear.y, t.linear.z, t.angular.z, time.monotonic()
        )

    def _tick(self):
        now = time.monotonic()
        for drone_state in self.backend.poll(now):
            self.guard.on_drone_state(drone_state, now)
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

    def _publish_state(self):
        state = self.guard.flight_state(time.monotonic())
        self._state_pub.publish(String(data=json.dumps(state, separators=(",", ":"))))

    def _on_estop(self, request, response):
        was_sending = self.guard.active and not self.guard.estop
        self._estop_reason = "service"
        self.guard.trigger_estop(self._estop_reason)
        if was_sending:
            self.backend.send_setpoint(ZERO, time.monotonic())  # one zero, then silence
        self._publish_state()
        self.get_logger().error("E-STOP: setpoints stopped; ~/reset_estop to clear")
        response.success = True
        response.message = "estop latched"
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
