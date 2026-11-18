# intent_node: /gesture/raw -> filter -> /gesture/intent
# Build context is drone/ground/; FROM base is docker/base/Dockerfile (see
# docker-compose.yml). Pure rclpy, no pip packages. Runs as the base's user `ros`.
FROM base

COPY --chown=ros:ros src/intent/intent_node src/intent_node
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select intent_node

# input_source defaults to udp; gesture_node publishes on /gesture/raw instead.
# The node itself, not `ros2 run` (which neither handles nor forwards
# SIGTERM), so `docker stop` stops it cleanly at once instead of killing it.
CMD ["/ws/install/intent_node/lib/intent_node/intent_node", "--ros-args", "-p", "input_source:=topic"]
