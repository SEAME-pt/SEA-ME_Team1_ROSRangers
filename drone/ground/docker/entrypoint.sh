#!/bin/bash
# Source ROS 2 and the workspace (if this image built one), then run the command.
set -e
source /opt/ros/jazzy/setup.bash
if [ -f /ws/install/setup.bash ]; then
    source /ws/install/setup.bash
fi
exec "$@"
