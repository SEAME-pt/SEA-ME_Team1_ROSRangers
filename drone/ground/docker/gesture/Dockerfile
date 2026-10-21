# gesture_node: webcam -> MediaPipe -> /gesture/raw
# Build context is drone/ground/ (see docker-compose.yml).
FROM ros:jazzy-ros-base

# pip, plus the X11/Qt libraries that opencv-python's cv2.imshow window needs
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
        libgl1 libegl1 libglib2.0-0 libsm6 libxext6 libxrender1 libfontconfig1 \
        libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 marks the system Python as externally managed (PEP 668).
# The container is throwaway, so install straight into it.
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1
COPY src/gesture_node/requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt

WORKDIR /ws
COPY src/gesture_node src/gesture_node
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select gesture_node

COPY docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "run", "gesture_node", "gesture_node"]
