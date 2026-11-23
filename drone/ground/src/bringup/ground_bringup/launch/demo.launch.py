"""Demo mode: gesture -> intent -> mission -> controller, with the PRETEND
/flight/state (fake_flight_state). Never with a real drone. The controller has
no measured velocity here, so it publishes inactive setpoints (ADR-025 F12)
and no flight_interface reads them.

    ros2 launch ground_bringup demo.launch.py
    ros2 launch ground_bringup demo.launch.py camera:=false
        (no gesture_node: publish /gesture/raw yourself, e.g. with
        gesture_node's test/fake_camera.py)
    ros2 launch ground_bringup demo.launch.py params_file:=/path/to/my.yaml

Parameters: config/demo.yaml (params_file replaces it). `make` runs the same
nodes, one per container, each with that file (docker-compose.yml).
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# Next to launch/, both in the source and in the installed share folder
CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "demo.yaml")


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
            # The controller (#199): inactive here, no measured velocity (F6, F12)
            Node(
                package="controller_node",
                executable="controller_node",
                parameters=[params],
                output="screen",
            ),
            # The only /flight/state publisher in this mode
            Node(
                package="mission_node",
                executable="fake_flight_state",
                parameters=[params],
                output="screen",
            ),
        ]
    )
