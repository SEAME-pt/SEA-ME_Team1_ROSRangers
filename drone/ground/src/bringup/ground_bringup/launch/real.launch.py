"""Real mode: gesture -> intent -> mission -> flight_interface -> the drone
(ADR-025). Follow the Safety Rules: props off on the bench, the safety pilot
holds the radio.

flight_interface starts unconfigured. Configure, activate and ARM stay
operator steps, never this file's (REQ-SAF-023):
    ros2 lifecycle set /flight_interface configure
    ros2 lifecycle set /flight_interface activate
    ros2 service call /flight_interface/arm_control std_srvs/srv/Trigger
No respawn, on purpose (ADR-025 A6): a dead node stays dead and the ESP's
link failsafe takes over. Do not add respawn=True.

ROS traffic stays on this PC (ADR-025 E1): unless they are already set, the
nodes run with ROS_DOMAIN_ID=42 and ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST,
as flight_interface's own launch file does. Every terminal that talks to them
needs the same two variables.

    ros2 launch ground_bringup real.launch.py
    ros2 launch ground_bringup real.launch.py camera:=false
    ros2 launch ground_bringup real.launch.py params_file:=/path/to/my.yaml

Parameters: config/real.yaml (params_file replaces it).
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node

# Next to launch/, both in the source and in the installed share folder
CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "real.yaml")
TEAM_DOMAIN_ID = "42"  # ADR-025 E1


def keep_or_default(name, default):
    """Leave the variable as the caller set it, else use the default."""
    return SetEnvironmentVariable(
        name, EnvironmentVariable(name, default_value=default)
    )


def generate_launch_description():
    params = LaunchConfiguration("params_file")
    return LaunchDescription(
        [
            keep_or_default("ROS_DOMAIN_ID", TEAM_DOMAIN_ID),
            keep_or_default("ROS_AUTOMATIC_DISCOVERY_RANGE", "LOCALHOST"),
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
            # The only /flight/state publisher in this mode. Unconfigured, no
            # respawn (A6).
            Node(
                package="flight_interface",
                executable="flight_interface",
                name="flight_interface",
                parameters=[params],
                output="screen",
            ),
        ]
    )
