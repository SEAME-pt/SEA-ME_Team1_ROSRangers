# controller_node: /mission/velocity_target + /estimate/velocity ->
# /control/attitude_setpoint (#199, ADR-025 C1, F1-F17).
# Build context is drone/ground/; FROM base is docker/base/Dockerfile (see
# docker-compose.yml). Pure rclpy, no pip packages. Runs as the base's user `ros`.
FROM base

COPY --chown=ros:ros src/controller/controller_node src/controller_node
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select controller_node

# The node itself, not `ros2 run` (which neither handles nor forwards
# SIGTERM), so `docker stop` stops it at once and its last neutral goes out.
CMD ["/ws/install/controller_node/lib/controller_node/controller_node"]
