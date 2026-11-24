# flight_interface (#103, ADR-025): the only ground node that talks to the drone.
# Build context is drone/ground/; FROM base is docker/base/Dockerfile (see
# docker-compose.yml). Runs as the base's user `ros`.
#
# Starts the node unconfigured, backend loopback unless backend:=esp is
# passed, no respawn (A6). Configure, activate and ARM stay
# operator actions (ros2 lifecycle / ros2 service), never this image's.
# The PSK is never in the image or its environment (ADR-025 E3): compose
# mounts ~/.config/seame-drone/psk.json (the PSK only, E3.2) read-only at
# run time, at the path the node reads (/home/ros/.config/seame-drone/secrets.json).
# The user has the host user's uid, so it can read that file (mode 600).
#
# Stages (the last one is the default, so a plain build is always the normal
# image):
#   flight_interface  the node; no px4_msgs. compose's flight_interface
#                     service (real mode), make smoke, CI.
#   sim               SIMULATION ONLY (#199, ADR-025 F3, F21): the same image
#                     plus px4_msgs, for the px4 backend. compose's
#                     flight_interface_sim service (make sim, make sim-smoke)
#                     builds it with target sim and the build context `px4`
#                     (the PX4 image, make px4-image).
FROM base AS flight_interface

# pip for sd_protocol, pyserial for msp.py (SerialTransport); pytest (in
# ros-base) runs the package's tests (make smoke runs them inside this image)
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip python3-serial \
    && rm -rf /var/lib/apt/lists/*

# sd_protocol: the ESP backend's session and frames (a pip package, not ROS)
COPY src/sd_protocol_py /tmp/sd_protocol_py
RUN pip3 install /tmp/sd_protocol_py && rm -rf /tmp/sd_protocol_py
USER ros

COPY --chown=ros:ros src/flight_interface/flight_interface src/flight_interface
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select flight_interface

# The node itself, not `ros2 launch`: launch ignores SIGTERM (docker stop
# then kills it after 10 s, without the last zero setpoint). The node sets
# its own SIGTERM and SIGINT handlers (link_node.main), which replace the
# SIGINT ignore bash gives a background command; entrypoint.sh forwards both
# as SIGTERM. On either, it deactivates (one last zero setpoint) and closes
# the backend. The launch file's E1
# defaults (ROS_DOMAIN_ID 42, LOCALHOST discovery) come from compose's
# environment instead. backend:=esp: --ros-args -p backend:=esp
CMD ["/ws/install/flight_interface/lib/flight_interface/flight_interface"]

# ---------- SIMULATION ONLY (#199, ADR-025 F21) ----------
# px4_msgs from the PX4 image, built again here. The PX4 image built it with
# --symlink-install: its install/ is links into its build/ and src/, so only
# the source (the .msg files, CMakeLists.txt, package.xml) is copied, and
# colcon builds real files into /ws/install next to flight_interface. Its
# release (release/1.17) matches PX4 v1.17's messages. Takes a few minutes
# the first time, then the layer is cached.
FROM flight_interface AS sim
COPY --from=px4 --chown=ros:ros /home/dev/ros2_px4_ws/src/px4_msgs src/px4_msgs
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select px4_msgs \
    && rm -rf build/px4_msgs log

# ---------- The default: the normal image (no px4_msgs) ----------
FROM flight_interface
