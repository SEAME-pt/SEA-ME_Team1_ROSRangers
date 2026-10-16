# PX4 SITL + Gazebo Harmonic + ROS 2 Jazzy on Ubuntu 24.04 (issue #29, ADR-018/019)
FROM ros:jazzy
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
