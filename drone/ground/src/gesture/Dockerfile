# gesture_node: webcam -> MediaPipe -> /gesture/raw
# Build context is drone/ground/; FROM base is docker/base/Dockerfile (see
# docker-compose.yml). Runs as the base's user `ros` (video group: the camera).
FROM base

# pip, plus the X11/Qt libraries that opencv-python's cv2.imshow window needs
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
        libgl1 libegl1 libglib2.0-0 libsm6 libxext6 libxrender1 libfontconfig1 \
        libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
    && rm -rf /var/lib/apt/lists/*
COPY src/gesture/gesture_node/requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt && rm /tmp/requirements.txt
USER ros

COPY --chown=ros:ros src/gesture/gesture_node src/gesture_node
RUN . /opt/ros/jazzy/setup.sh && colcon build --packages-select gesture_node

# The node itself, not `ros2 run` (which neither handles nor forwards
# SIGTERM), so `docker stop` stops it cleanly at once instead of killing it.
CMD ["/ws/install/gesture_node/lib/gesture_node/gesture_node"]
