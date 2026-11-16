# flight_interface (#103, ADR-025): the only ground node that talks to the drone.
# Build context is drone/ground/ (see docker-compose.yml).
#
# Starts the node unconfigured, backend loopback unless backend:=esp is
# passed, no respawn (A6). Configure, activate and ARM stay
# operator actions (ros2 lifecycle / ros2 service), never this image's.
# The PSK is never in the image or its environment (ADR-025 E3): compose
# mounts ~/.config/seame-drone/secrets.json read-only at run time.
FROM ros:jazzy-ros-base

# pip for sd_protocol, pyserial for msp.py (SerialTransport), pytest for the
# package's tests (make smoke runs them inside this image)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip python3-serial python3-pytest \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 marks the system Python as externally managed (PEP 668).
# The container is throwaway, so install straight into it.
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1

# Before the source: it rarely changes, so its layer stays cached
COPY docker/entrypoint.sh /entrypoint.sh

# sd_protocol: the ESP backend's session and frames (a pip package, not ROS)
COPY src/sd_protocol_py /tmp/sd_protocol_py
RUN pip3 install /tmp/sd_protocol_py && rm -rf /tmp/sd_protocol_py

WORKDIR /ws
COPY src/flight_interface/flight_interface src/flight_interface
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select flight_interface

ENTRYPOINT ["/entrypoint.sh"]
# The node itself, not `ros2 launch`: launch ignores SIGTERM (docker stop
# then kills it after 10 s, without the last zero setpoint), and SIGINT never
# reaches it under entrypoint.sh (bash ignores SIGINT for the commands it
# starts in the background). The node handles SIGTERM itself: it deactivates
# (one last zero setpoint) and closes the backend. The launch file's E1
# defaults (ROS_DOMAIN_ID 42, LOCALHOST discovery) come from compose's
# environment instead. backend:=esp: --ros-args -p backend:=esp
CMD ["/ws/install/flight_interface/lib/flight_interface/flight_interface"]
