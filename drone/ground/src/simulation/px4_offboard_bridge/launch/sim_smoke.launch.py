"""Ground chain against PX4 SITL (SIMULATION ONLY, #86).

intent_node (input_source:=topic, reads /gesture/raw) -> mission_node ->
px4_offboard_bridge -> PX4. The bridge also publishes /flight/state, so no
fake_flight_state runs. Gesture input comes from the scenario (fake
/gesture/raw), not a camera. PX4 SITL and the Micro XRCE-DDS agent must
already run (sim_smoke.sh starts them).

    ros2 launch px4_offboard_bridge sim_smoke.launch.py            # mask 15
    ros2 launch px4_offboard_bridge sim_smoke.launch.py mask:=11   # z stays still
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    mask = LaunchConfiguration("mask")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "mask", default_value="15", description="8, 11 or 15"
            ),
            Node(
                package="intent_node",
                executable="intent_node",
                parameters=[{"input_source": "topic"}],
                output="screen",
            ),
            Node(package="mission_node", executable="mission_node", output="screen"),
            Node(
                package="px4_offboard_bridge",
                executable="px4_offboard_bridge",
                parameters=[{"mask": ParameterValue(mask, value_type=int)}],
                output="screen",
            ),
        ]
    )
