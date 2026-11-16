"""Run a node until Ctrl+C or SIGTERM (docker stop), then stop cleanly.

Same pattern as intent_node's main(): the signals only ask the spin loop to
stop. rclpy's own handler would shut rclpy down from another thread, so the
node could no longer publish (mission_node's final zero target) and the
executor could fail with an RCLError. Exit code 0 either way.

A node that cannot start (a parameter of the wrong type) logs FATAL and
exits with code 1, without a traceback.
"""

import signal
import sys
import threading

import rclpy
from rclpy.exceptions import ParameterException
from rclpy.executors import SingleThreadedExecutor
from rclpy.logging import get_logger
from rclpy.signals import SignalHandlerOptions


def run(make_node, finished=None, on_stop=None, args=None):
    """Spin the node made by `make_node()` until a signal, or until
    `finished(node)` is true. `on_stop(node)` runs before the node is destroyed,
    while it can still publish."""
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stop.set())
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    try:
        node = make_node()
    except (ValueError, ParameterException) as error:
        get_logger("mission_node").fatal(str(error))
        rclpy.try_shutdown()
        sys.exit(1)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    try:
        while not stop.is_set() and not (finished and finished(node)):
            executor.spin_once(timeout_sec=0.1)  # a signal is seen within 0.1 s
    finally:
        # Stopping now: ignore more Ctrl+C / SIGTERM (ros2 launch forwards one
        # more). A signal arriving while the interpreter exits would kill it.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        if on_stop is not None:
            on_stop(node)
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
