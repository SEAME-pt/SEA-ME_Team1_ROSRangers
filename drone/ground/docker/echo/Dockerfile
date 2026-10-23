# echo: live table of the gesture -> intent -> mission chain, redrawn in place
# (docker/echo/monitor.py). Shown by `docker compose up` (the nodes' output goes
# to logs/), or alone in its own terminal:
#   docker compose run --rm echo
# Nothing to build: rclpy, std_msgs and geometry_msgs ship with ros-base.
# Build context is drone/ground/ (see docker-compose.yml).
FROM ros:jazzy-ros-base

COPY docker/entrypoint.sh docker/echo/monitor.py /
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "/monitor.py"]
