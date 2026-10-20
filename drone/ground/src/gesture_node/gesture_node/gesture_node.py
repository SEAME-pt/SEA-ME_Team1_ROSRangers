#!/usr/bin/env python3
"""gesture_node: webcam -> MediaPipe (gestures.py) -> /gesture/raw.

Runs gestures.main() as it is: it opens cv2.VideoCapture(0), shows the
"Gestures" window and calls send_gesture() once per frame. send_gesture()
publishes the same JSON that the UDP gesture_sender.py sent
({"gesture", "t", "xy"}) as a std_msgs/String on /gesture/raw.
Press q in the window to stop.

The camera loop owns the main thread (OpenCV windows must stay there), so
rclpy spins in a background thread to answer `ros2 param` / `ros2 node info`.
"""

import sys
import threading

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from gesture_node import gesture_sender, gestures


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
            return  # shutting down (Ctrl+C): drop the packet
        msg = String()
        msg.data = text
        self._pub.publish(msg)

    def destroy_node(self):
        gesture_sender.set_publisher(None)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = GestureNode()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    try:
        gestures.main()
        # gestures.main() also returns at once when camera 0 cannot be opened.
        node.get_logger().info(
            "Camera loop ended (q pressed, camera 0 not available or no frames)"
        )
    except KeyboardInterrupt:
        pass
    finally:
        # Stop the spin thread and wait for it before tearing down. If it is
        # still inside rclpy's C++ wait when the interpreter exits, Python kills
        # it mid-call and the process aborts ("terminate called without an
        # active exception").
        if rclpy.ok():
            rclpy.shutdown()  # wakes the executor's wait set
        executor.shutdown()
        spinner.join(timeout=2.0)
        node.destroy_node()
    sys.exit(0)


if __name__ == "__main__":
    main()
