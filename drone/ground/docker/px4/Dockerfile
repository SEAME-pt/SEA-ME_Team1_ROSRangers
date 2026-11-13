# SIMULATION ONLY (make sim, make sim-smoke, #86): PX4 SITL + Gazebo + Micro
# XRCE-DDS agent from the PX4 image (drone/simulation/px4/Dockerfile, built by
# the Makefile as seame-px4-sim:jazzy-1.17), plus intent_node, mission_node and
# px4_offboard_bridge (it imports mission_node.spin; the smoke scenario runs
# intent_node). Build context is drone/ground/; the bridge comes from the extra
# context "sim" (docker-compose.yml). Never use with the real drone.
ARG PX4_IMAGE=seame-px4-sim:jazzy-1.17
FROM ${PX4_IMAGE}

USER root
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/px4/sim_start.sh /sim_start.sh
RUN mkdir -p /ws && chown dev:dev /ws
USER dev
WORKDIR /ws

COPY --chown=dev:dev src/intent_node src/intent_node
COPY --chown=dev:dev src/mission_node src/mission_node
COPY --chown=dev:dev --from=sim px4_offboard_bridge src/px4_offboard_bridge
# On top of the PX4 workspace (px4_msgs), so /ws/install/setup.bash sources both
RUN . /opt/ros/jazzy/setup.sh && . /home/dev/ros2_px4_ws/install/setup.sh \
    && colcon build --packages-select intent_node mission_node px4_offboard_bridge

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/sim_start.sh"]
