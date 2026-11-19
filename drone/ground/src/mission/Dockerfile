# mission_node: /gesture/intent + /flight/state -> /mission/velocity_target
# Same image runs fake_flight_state (pretend /flight/state, never with a real drone).
# Build context is drone/ground/; FROM base is docker/base/Dockerfile (see
# docker-compose.yml). Pure rclpy, no pip packages. Runs as the base's user `ros`.
FROM base

COPY --chown=ros:ros src/mission/mission_node src/mission_node
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select mission_node

# The node itself, not `ros2 run` (which neither handles nor forwards
# SIGTERM), so `docker stop` stops it at once and its last zero target goes out.
CMD ["/ws/install/mission_node/lib/mission_node/mission_node"]
