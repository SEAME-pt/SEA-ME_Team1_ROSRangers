#!/usr/bin/env python3
"""fake_flight_state: publishes a pretend /flight/state until flight_interface exists.

Every value is a parameter and is read on every tick, so it can be changed
while running:

    ros2 param set /fake_flight_state mask 15
    ros2 param set /fake_flight_state override_active false

Faults, to see mission_node fall back to IDLE:

    ros2 param set /fake_flight_state publish false     # flight_interface died
    ros2 param set /fake_flight_state raw 'not json'    # any text, sent as is
    ros2 param set /fake_flight_state raw "'{\"link_ok\": \"true\"}'"  # wrong type
    ros2 param set /fake_flight_state raw ''            # back to the fields

(JSON for `raw` needs the extra quotes, or ros2 param reads it as YAML.)
"""

from rclpy.node import Node
from std_msgs.msg import String

from mission_node.fake_inputs import flight_state_json
from mission_node.spin import run


class FakeFlightState(Node):
    def __init__(self, **kwargs):
        super().__init__("fake_flight_state", **kwargs)
        self.declare_parameter("topic", "/flight/state")
        self.declare_parameter("rate_hz", 10.0)
        self.declare_parameter("link_ok", True)
        self.declare_parameter("esp_state", "STREAMING")
        self.declare_parameter("override_active", True)
        self.declare_parameter("mask", 11)
        self.declare_parameter("airborne", True)
        self.declare_parameter("publish", True)  # false: send nothing at all
        self.declare_parameter("raw", "")  # not empty: sent as is, fields ignored

        topic = str(self.get_parameter("topic").value)
        rate = max(1.0, float(self.get_parameter("rate_hz").value))
        self._pub = self.create_publisher(String, topic, 10)
        self._last = None
        self.create_timer(1.0 / rate, self._tick)
        self.get_logger().warning(
            f"PRETEND flight state on {topic} at {rate:.0f} Hz. "
            "Not for use with a real drone."
        )

    def _tick(self):
        def p(name):
            return self.get_parameter(name).value

        if not p("publish"):
            text = None
        elif p("raw"):
            text = str(p("raw"))
        else:
            text = flight_state_json(
                p("link_ok"),
                p("esp_state"),
                p("override_active"),
                p("mask"),
                p("airborne"),
            )
        if text is not None:
            self._pub.publish(String(data=text))
        if text != self._last:
            self._last = text
            if text is None:
                self.get_logger().warning("Publishing nothing (publish:=false)")
            else:
                self.get_logger().info(f"Publishing {text}")


def main(args=None):
    run(FakeFlightState, args=args)


if __name__ == "__main__":
    main()
