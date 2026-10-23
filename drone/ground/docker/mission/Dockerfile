# mission_node: /gesture/intent + /flight/state -> /mission/velocity_target
# Same image runs fake_flight_state (pretend /flight/state, never with a real drone).
# Build context is drone/ground/ (see docker-compose.yml). Pure rclpy, no pip packages.
FROM ros:jazzy-ros-base

# Before the source: it rarely changes, so its layer stays cached
COPY docker/entrypoint.sh /entrypoint.sh
WORKDIR /ws
COPY src/mission_node src/mission_node
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select mission_node

ENTRYPOINT ["/entrypoint.sh"]
# The node itself is PID 1, not `ros2 run` (which neither handles nor forwards
# SIGTERM), so `docker stop` stops it at once and its last zero target goes out.
CMD ["/ws/install/mission_node/lib/mission_node/mission_node"]
