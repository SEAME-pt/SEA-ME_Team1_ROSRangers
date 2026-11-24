# SIMULATION ONLY (#29, #86, ADR-018/019). Never use with the real drone.
# Build context is drone/ground/ (its .dockerignore keeps build/, install/ and
# the logs out), for compose and for make px4-image alike. Two stages:
#   px4  PX4 SITL + Gazebo Harmonic + ROS 2 Jazzy (Ubuntu 24.04), px4_msgs and the
#        Micro XRCE-DDS agent. Long to build (several GB). The Makefile builds it
#        alone (make px4-image: --target px4, tagged seame-px4-sim:jazzy-1.17),
#        so make clean and fclean keep it.
#   sim  (final) PX4_IMAGE + intent_node and sim_smoke (the scenario of make
#        sim-smoke runs its own intent_node). The px4 service runs
#        sim_start.sh (make sim, make sim-smoke); the sim_smoke service runs the
#        scenario. flight_interface (px4 backend) is its own image (#199,
#        src/flight_interface/Dockerfile, stage sim). Compose sets
#        PX4_IMAGE to the tagged image (the Makefile's PX4_IMAGE). Without it
#        (a plain docker build) the px4 stage is built here too, which takes
#        long: use make px4-image, then make sim.
ARG PX4_IMAGE=px4

FROM ros:jazzy AS px4
ARG PX4_REF=v1.17.0
ARG PX4_MSGS_REF=release/1.17
ARG XRCE_REF=v2.4.3
ENV DEBIAN_FRONTEND=noninteractive LANG=en_US.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
      sudo git curl lsb-release ca-certificates locales ros-dev-tools \
      ros-jazzy-ros-base python3-pip \
    && locale-gen en_US.UTF-8 && rm -rf /var/lib/apt/lists/*

# Non-root user: PX4's ubuntu.sh calls sudo and refuses root-less assumptions
RUN (id ubuntu >/dev/null 2>&1 && userdel -r ubuntu || true) \
    && useradd -m -s /bin/bash -u 1000 dev \
    && echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev
USER dev
WORKDIR /home/dev

RUN git clone --depth 1 --branch ${PX4_REF} --recursive --shallow-submodules \
      https://github.com/PX4/PX4-Autopilot.git \
    && bash PX4-Autopilot/Tools/setup/ubuntu.sh --no-nuttx

# ROS 2 workspace: px4_msgs, px4_ros_com, Micro XRCE-DDS Agent
RUN mkdir -p ros2_px4_ws/src && cd ros2_px4_ws/src \
    && git clone --depth 1 -b ${PX4_MSGS_REF} https://github.com/PX4/px4_msgs.git \
    && git clone --depth 1 https://github.com/PX4/px4_ros_com.git \
    && git clone --depth 1 -b ${XRCE_REF} https://github.com/eProsima/Micro-XRCE-DDS-Agent.git \
    && cd .. && . /opt/ros/jazzy/setup.sh && colcon build --symlink-install

# Build PX4 SITL once so the first run is fast
RUN cd PX4-Autopilot && make px4_sitl_default

RUN echo 'source /opt/ros/jazzy/setup.bash; source ~/ros2_px4_ws/install/setup.bash' >> ~/.bashrc
CMD ["bash"]

FROM ${PX4_IMAGE} AS sim
USER root
COPY docker/entrypoint.sh /entrypoint.sh
COPY src/simulation/sim_start.sh /sim_start.sh
RUN mkdir -p /ws && chown dev:dev /ws
USER dev
WORKDIR /ws

COPY --chown=dev:dev src/intent/intent_node src/intent_node
COPY --chown=dev:dev src/simulation/sim_smoke src/sim_smoke
# On top of the PX4 workspace (px4_msgs), so /ws/install/setup.bash sources both
RUN . /opt/ros/jazzy/setup.sh && . /home/dev/ros2_px4_ws/install/setup.sh \
    && colcon build --packages-select intent_node sim_smoke

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/sim_start.sh"]
