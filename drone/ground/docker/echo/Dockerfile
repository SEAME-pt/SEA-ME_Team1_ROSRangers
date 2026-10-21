# echo: prints /gesture/intent. Nothing to build, std_msgs ships with ros-base.
# Build context is drone/ground/ (see docker-compose.yml).
FROM ros:jazzy-ros-base

COPY docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
# Type given explicitly so it does not fail if intent hasn't started yet
CMD ["ros2", "topic", "echo", "/gesture/intent", "std_msgs/msg/String"]
