#!/usr/bin/env python3
"""gesture_node: webcam -> MediaPipe (gestures.py) -> /gesture/raw.

Runs the camera loop gestures.main(): it opens cv2.VideoCapture(0), shows the
"Gestures" window and calls send_gesture() once per frame. send_gesture()
publishes the same JSON that the UDP gesture_sender.py sent
({"gesture", "t", "xy"}) as a std_msgs/String on /gesture/raw.

Stop with q in the window, Ctrl+C or SIGTERM (docker stop): exit code 0.
Camera 0 missing or lost: error log and exit code 1.

The camera loop owns the main thread (OpenCV windows must stay there), so
rclpy spins in a background thread to answer `ros2 param` / `ros2 node info`.
"""

import signal
import sys
import threading

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import String

from gesture_node import gesture_sender, gestures

# Why the camera loop ended -> (exit code, log message). Exit code 1 logs an error.
ENDINGS = {
    gestures.QUIT: (0, "q pressed in the Gestures window"),
    gestures.STOPPED: (0, "stopped (Ctrl+C or SIGTERM)"),
    gestures.NO_CAMERA: (
        1,
        "Cannot open camera 0. Is a webcam connected, and not used by another program?",
    ),
    gestures.CAMERA_LOST: (1, "Camera 0 stopped delivering frames (unplugged?)"),
}


class GestureNode(Node):
    def __init__(self):
        super().__init__("gesture_node")

        self.declare_parameter("raw_topic", "/gesture/raw")
        topic = str(self.get_parameter("raw_topic").value)

        self._pub = self.create_publisher(String, topic, 10)
        gesture_sender.set_publisher(self._publish)

        self.get_logger().info(
            f"Reading camera 0 with MediaPipe; publishing JSON strings on {topic}. "
            "Press q in the Gestures window to stop."
        )

    def _publish(self, text):
        if not rclpy.ok():
            return  # shutting down: drop the packet
        msg = String()
        msg.data = text
        self._pub.publish(msg)

    def destroy_node(self):
        gesture_sender.set_publisher(None)
        super().destroy_node()


def main(args=None):
    # Ctrl+C and SIGTERM only ask the camera loop to stop (it checks once per
    # frame). rclpy's own handler would shut rclpy down from another thread
    # while the spin thread builds its next wait set, which then fails with
    # an RCLError. A repeated signal (ros2 launch forwards Ctrl+C) changes
    # nothing.
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stop.set())
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    node = GestureNode()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    try:
        ending = gestures.main(should_stop=stop.is_set)
    finally:
        # Stopping now: ignore more Ctrl+C / SIGTERM (ros2 launch forwards one
        # more). A Python handler is removed while the interpreter exits, and a
        # signal arriving then would kill the process (exit code -2).
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        # Stop the executor BEFORE rclpy, so spin() returns normally. A spin
        # thread still inside rclpy's C++ wait when the interpreter exits
        # aborts the process ("terminate called without an active exception").
        executor.shutdown()
        spinner.join(timeout=2.0)

    exit_code, message = ENDINGS[ending]
    log = node.get_logger()
    (log.error if exit_code else log.info)(f"Camera loop ended: {message}")
    node.destroy_node()
    rclpy.shutdown()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
