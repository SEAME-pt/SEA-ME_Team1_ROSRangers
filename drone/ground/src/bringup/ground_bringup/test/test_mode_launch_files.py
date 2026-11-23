"""Static checks of the mode launch files (demo, real, sim).

Loads each file in launch/ and checks the nodes it describes, without
starting them:
  * each mode starts the nodes it should, and exactly one /flight/state
    publisher: fake_flight_state (demo), flight_interface (real), none in sim
    (px4_offboard_bridge runs in the px4 container);
  * every node gets the mode's parameter file, and the default file exists;
  * camera:=false leaves gesture_node out; nothing respawns (ADR-025 A6);
  * real keeps ADR-025 E1's ROS_DOMAIN_ID / LOCALHOST defaults.
drone/tests/ros/test_demo_launch.py runs demo.launch.py for real.

Needs the ROS 2 launch packages (ROS 2 Jazzy sourced). Without them the tests
are skipped, unless REQUIRE_ROS_TESTS=1 is set (CI sets it).

Run with:  colcon test --packages-select ground_bringup
or:        python3 -m pytest test -v   (package folder, ROS sourced)
"""

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

try:
    from launch import LaunchContext
    from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
    from launch.substitutions import LaunchConfiguration
    from launch_ros.actions import Node
    from launch_ros.parameter_descriptions import ParameterFile
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    Node = None

PACKAGE = Path(__file__).resolve().parents[1]
MODES = ("demo", "real", "sim")
GROUND_CHAIN = {
    ("gesture_node", "gesture_node"),
    ("intent_node", "intent_node"),
    ("mission_node", "mission_node"),
}
CONTROLLER = ("controller_node", "controller_node")  # demo, real (#199)
FLIGHT_STATE_PUBLISHERS = {
    ("mission_node", "fake_flight_state"),
    ("flight_interface", "flight_interface"),
}


def entities(mode):
    path = PACKAGE / "launch" / f"{mode}.launch.py"
    spec = importlib.util.spec_from_file_location(f"{mode}_launch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_launch_description().entities


def nodes(mode):
    """{(package, executable): Node} for each Node in the mode's launch file."""
    return {
        (e.node_package, e.node_executable): e
        for e in entities(mode)
        if isinstance(e, Node)
    }


def arguments(mode):
    return {
        e.name: e.default_value
        for e in entities(mode)
        if isinstance(e, DeclareLaunchArgument)
    }


def text(value):
    """Plain text of a launch value made of TextSubstitutions."""
    if isinstance(value, (list, tuple)):
        return "".join(text(part) for part in value)
    return getattr(value, "text", value)


@unittest.skipIf(Node is None, "ROS 2 launch packages not available")
class ModeLaunchTests(unittest.TestCase):
    def test_each_mode_starts_its_nodes(self):
        expected = {
            "demo": GROUND_CHAIN | {CONTROLLER, ("mission_node", "fake_flight_state")},
            "real": GROUND_CHAIN
            | {CONTROLLER, ("flight_interface", "flight_interface")},
            "sim": GROUND_CHAIN,
        }
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(set(nodes(mode)), expected[mode])

    def test_at_most_one_flight_state_publisher(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                found = set(nodes(mode)) & FLIGHT_STATE_PUBLISHERS
                self.assertLessEqual(len(found), 1, found)

    def test_every_node_gets_the_params_file(self):
        for mode in MODES:
            for key, node in nodes(mode).items():
                with self.subTest(mode=mode, node=key):
                    (group,) = node._Node__parameters  # no public getter before launch
                    self.assertIsInstance(group, ParameterFile)
                    (path,) = group.param_file
                    self.assertIsInstance(path, LaunchConfiguration)
                    self.assertEqual(text(path.variable_name), "params_file")

    def test_default_params_file_is_the_mode_config(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                default = Path(text(arguments(mode)["params_file"])).resolve()
                self.assertEqual(default, PACKAGE / "config" / f"{mode}.yaml")
                self.assertTrue(default.is_file())

    def test_camera_false_leaves_gesture_node_out(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(text(arguments(mode)["camera"]), "true")
                gesture = nodes(mode)[("gesture_node", "gesture_node")]
                context = LaunchContext()
                context.launch_configurations["camera"] = "false"
                self.assertFalse(gesture.condition.evaluate(context))
                context.launch_configurations["camera"] = "true"
                self.assertTrue(gesture.condition.evaluate(context))
                for key, node in nodes(mode).items():
                    if key != ("gesture_node", "gesture_node"):
                        self.assertIsNone(node.condition, key)

    def test_nothing_respawns(self):
        for mode in MODES:
            for key, node in nodes(mode).items():
                with self.subTest(mode=mode, node=key):
                    self.assertFalse(node._ExecuteLocal__respawn)

    def test_real_keeps_the_e1_defaults(self):
        env = {
            text(e.name): e.value
            for e in entities("real")
            if isinstance(e, SetEnvironmentVariable)
        }
        self.assertEqual(set(env), {"ROS_DOMAIN_ID", "ROS_AUTOMATIC_DISCOVERY_RANGE"})
        context = LaunchContext()
        # Neither variable set by the caller: the defaults apply
        with mock.patch.dict(os.environ):
            for name in env:
                os.environ.pop(name, None)
            resolved = {
                name: "".join(s.perform(context) for s in value)
                for name, value in env.items()
            }
        self.assertEqual(
            resolved,
            {"ROS_DOMAIN_ID": "42", "ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"},
        )


if __name__ == "__main__":
    unittest.main()
