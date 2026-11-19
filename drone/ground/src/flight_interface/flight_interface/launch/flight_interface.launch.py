"""Starts the flight_interface lifecycle node (#103), unconfigured.

    ros2 launch flight_interface flight_interface.launch.py backend:=esp esp_host:=192.168.4.1

Nothing is configured or activated here: the operator does it with
`ros2 lifecycle set /flight_interface configure` and `activate`.

No respawn, on purpose (decision A6): the E-stop latch lives in the process,
so a restarted node would start without it. A dead node stays dead; the ESP's
link failsafe takes over, and bringing the node back is an operator action
(configure, activate, then an operator ARM). Do not add respawn=True.

ROS traffic stays on this PC (decision E1): unless they are already set, the
node runs with ROS_DOMAIN_ID=42 (the team domain) and
ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST, so another laptop on the drone's
Wi-Fi cannot call ~/arm_control, ~/reset_estop or ~/resume. Every terminal
that talks to the node (ros2 lifecycle, ros2 service, mission_node) needs the
same two variables:

    export ROS_DOMAIN_ID=42 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node

TEAM_DOMAIN_ID = "42"  # E1: not 0, outside the tests' range 81-98


def keep_or_default(name, default):
    """Leave the variable as the caller set it, else use the default."""
    return SetEnvironmentVariable(
        name, EnvironmentVariable(name, default_value=default)
    )


def generate_launch_description():
    return LaunchDescription(
        [
            keep_or_default("ROS_DOMAIN_ID", TEAM_DOMAIN_ID),
            keep_or_default("ROS_AUTOMATIC_DISCOVERY_RANGE", "LOCALHOST"),
            DeclareLaunchArgument("backend", default_value="loopback"),
            DeclareLaunchArgument("esp_host", default_value="192.168.4.1"),
            Node(
                package="flight_interface",
                executable="flight_interface",
                name="flight_interface",
                output="screen",
                parameters=[
                    {
                        "backend": LaunchConfiguration("backend"),
                        "esp_host": LaunchConfiguration("esp_host"),
                    }
                ],
            ),
        ]
    )
