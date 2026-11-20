# echo: live table of the gesture -> intent -> mission chain, redrawn in place
# (src/echo/monitor.py). Shown by `docker compose up` (the nodes' output goes
# to logs/), or alone in its own terminal:
#   docker compose run --rm echo
# Nothing to build: rclpy, std_msgs, geometry_msgs and sensor_msgs ship with ros-base.
# Build context is drone/ground/; FROM base is docker/base/Dockerfile (see
# docker-compose.yml). Runs as the base's user `ros`.
FROM base

COPY src/echo/monitor.py /monitor.py
CMD ["python3", "/monitor.py"]
