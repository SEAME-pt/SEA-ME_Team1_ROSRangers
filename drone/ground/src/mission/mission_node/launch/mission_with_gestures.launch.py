"""intent_node + mission_node + a pretend flight state: the real gesture chain.

Start the recogniser in another terminal (`ros2 run gesture_node gesture_node`,
or test/fake_camera.py in gesture_node as a fake camera): it publishes
/gesture/raw, intent_node turns it into /gesture/intent. Both packages must be
built in the same workspace.

    ros2 launch mission_node mission_with_gestures.launch.py
    ros2 launch mission_node mission_with_gestures.launch.py mask:=15
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    mask = LaunchConfiguration("mask")
    airborne = LaunchConfiguration("airborne")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "mask", default_value="11", description="8, 11 or 15"
            ),
            DeclareLaunchArgument("airborne", default_value="true"),
            Node(
                package="intent_node",
                executable="intent_node",
                parameters=[{"input_source": "topic"}],
                output="screen",
            ),
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
        ]
    )
