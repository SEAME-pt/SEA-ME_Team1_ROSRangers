# intent_node: /gesture/raw -> filter -> /gesture/intent
# Build context is drone/ground/ (see docker-compose.yml). Pure rclpy, no pip packages.
FROM ros:jazzy-ros-base

# Before the source: it rarely changes, so its layer stays cached
COPY docker/entrypoint.sh /entrypoint.sh
WORKDIR /ws
COPY src/intent_node src/intent_node
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select intent_node

ENTRYPOINT ["/entrypoint.sh"]
# input_source defaults to udp; gesture_node publishes on /gesture/raw instead.
# The node itself is PID 1, not `ros2 run` (which neither handles nor forwards
# SIGTERM), so `docker stop` stops it cleanly at once instead of killing it.
CMD ["/ws/install/intent_node/lib/intent_node/intent_node", "--ros-args", "-p", "input_source:=topic"]
