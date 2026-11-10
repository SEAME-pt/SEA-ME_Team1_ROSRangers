"""Starts the flight_interface lifecycle node (#103), unconfigured.

    ros2 launch flight_interface flight_interface.launch.py backend:=esp esp_host:=192.168.4.1

Nothing is configured or activated here: the operator does it with
`ros2 lifecycle set /flight_interface configure` and `activate`.

No respawn, on purpose (decision A6): the E-stop latch lives in the process,
so a restarted node would start without it. A dead node stays dead; the ESP's
link failsafe takes over, and bringing the node back is an operator action
(configure, activate, then an operator ARM). Do not add respawn=True.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
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
