"""Simulation mode: the ground nodes (gesture -> intent -> mission) against
PX4 SITL + Gazebo. SIMULATION ONLY.

Starts no /flight/state publisher: in the simulation it is px4_offboard_bridge,
which runs in the px4 container with PX4 and the XRCE agent (make sim). It
needs px4_msgs, so this package does not depend on it.

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
        ]
    )
