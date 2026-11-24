"""Simulation mode: the ground nodes (gesture -> intent -> mission ->
controller) against PX4 SITL + Gazebo. SIMULATION ONLY.

Starts no /flight/state publisher: in the simulation it is flight_interface
with the px4 backend (#199), which needs px4_msgs, so only its simulation
image runs it (make sim: the flight_interface_sim service), never this file.
This package does not depend on px4_msgs.

    ros2 launch ground_bringup sim.launch.py
    ros2 launch ground_bringup sim.launch.py camera:=false
    ros2 launch ground_bringup sim.launch.py params_file:=/path/to/my.yaml

Parameters: config/sim.yaml (params_file replaces it).
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# Next to launch/, both in the source and in the installed share folder
CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "sim.yaml")


def generate_launch_description():
    params = LaunchConfiguration("params_file")
    return LaunchDescription(
        [
            DeclareLaunchArgument("params_file", default_value=CONFIG),
            DeclareLaunchArgument(
                "camera", default_value="true", description="false: no gesture_node"
            ),
            Node(
                package="gesture_node",
                executable="gesture_node",
                parameters=[params],
                output="screen",
                condition=IfCondition(LaunchConfiguration("camera")),
            ),
            Node(
                package="intent_node",
                executable="intent_node",
                parameters=[params],
                output="screen",
            ),
            Node(
                package="mission_node",
                executable="mission_node",
                parameters=[params],
                output="screen",
            ),
            # The controller (#199): flies once flight_interface's px4 backend
            # publishes /estimate/velocity (sim.yaml sets the hover throttle)
            Node(
                package="controller_node",
                executable="controller_node",
                parameters=[params],
                output="screen",
            ),
        ]
    )
