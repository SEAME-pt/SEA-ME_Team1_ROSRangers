#!/usr/bin/env python3
"""fake_intent: publishes pretend /gesture/intent messages, like intent_node.

    ros2 run mission_node fake_intent                       # scripted demo (~29 s)
    ros2 run mission_node fake_intent --ros-args -p loop:=true
    ros2 run mission_node fake_intent --ros-args -p mode:=constant
    ros2 param set /fake_intent intent ENGAGE_OR_HOLD        # then MOVE_RIGHT, ...
    ros2 param set /fake_intent reason no_hand_long         # constant: the reason sent

Do not run it together with intent_node: both publish on /gesture/intent.
"""

import time

from rclpy.node import Node
from std_msgs.msg import String

from mission_node.fake_inputs import DEMO_SCRIPT, intent_json, script_duration, step_at
from mission_node.spin import run


class FakeIntent(Node):
    def __init__(self, **kwargs):
        super().__init__("fake_intent", **kwargs)
        self.declare_parameter("topic", "/gesture/intent")
        self.declare_parameter("rate_hz", 20.0)
        self.declare_parameter("mode", "demo")  # "demo" or "constant"
        self.declare_parameter("intent", "NEUTRAL")  # used by "constant"
        self.declare_parameter("reason", "ok")  # used by "constant"
        self.declare_parameter("loop", False)  # demo: start again at the end

        topic = str(self.get_parameter("topic").value)
        rate = max(1.0, float(self.get_parameter("rate_hz").value))
        self._mode = str(self.get_parameter("mode").value).strip().lower()
        self._loop = bool(self.get_parameter("loop").value)
        self._pub = self.create_publisher(String, topic, 10)
        self._start = time.monotonic()
        self._step = None
        self.finished = False
        self.create_timer(1.0 / rate, self._tick)

        if self._mode == "constant":
            self.get_logger().info(f"Publishing a constant intent on {topic}")
        else:
            self._mode = "demo"
            self.get_logger().info(
                f"Playing the demo script on {topic} "
                f"({script_duration():.0f} s, loop={self._loop})"
            )

    def _publish(self, intent, reason):
        msg = String()
        msg.data = intent_json(intent, reason, time.time())
        self._pub.publish(msg)

    def _tick(self):
        if self._mode == "constant":
            intent = str(self.get_parameter("intent").value)
            self._publish(intent, str(self.get_parameter("reason").value))
            if intent != self._step:
                self._step = intent
                self.get_logger().info(f"Intent {intent}")
            return

        step = step_at(time.monotonic() - self._start)
        if step is None:
            if self._loop:
                self._start = time.monotonic()
                return
            self.get_logger().info("Demo finished")
            self.finished = True
            return
        index, intent, reason = step
        if index != self._step:
            self._step = index
            label = "(silence: nothing published)" if intent is None else intent
            self.get_logger().info(f"Step {index + 1}/{len(DEMO_SCRIPT)}: {label}")
        if intent is not None:
            self._publish(intent, reason)


def main(args=None):
    run(FakeIntent, finished=lambda node: node.finished, args=args)


if __name__ == "__main__":
    main()
