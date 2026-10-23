"""mission_node on its own, with pretend inputs (no intent_node, no drone).

ros2 launch mission_node mission_demo.launch.py
ros2 launch mission_node mission_demo.launch.py mask:=15 airborne:=false loop:=true
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    mask = LaunchConfiguration("mask")
    airborne = LaunchConfiguration("airborne")
    loop = LaunchConfiguration("loop")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "mask", default_value="11", description="8, 11 or 15"
            ),
            DeclareLaunchArgument("airborne", default_value="true"),
            DeclareLaunchArgument("loop", default_value="false"),
            Node(
                package="mission_node",
                executable="fake_flight_state",
                parameters=[
                    {
                        "mask": ParameterValue(mask, value_type=int),
                        "airborne": ParameterValue(airborne, value_type=bool),
                    }
                ],
                output="screen",
            ),
            Node(package="mission_node", executable="mission_node", output="screen"),
            Node(
                package="mission_node",
                executable="fake_intent",
                parameters=[{"loop": ParameterValue(loop, value_type=bool)}],
                output="screen",
            ),
        ]
    )
