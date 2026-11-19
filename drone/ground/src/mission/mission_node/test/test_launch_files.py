"""Static checks of the launch files: which nodes they start, with what.

Loads each file in launch/ and checks the nodes it describes, without
starting them: the right packages and executables, every mission_node
executable exists in setup.py, and mission_with_gestures.launch.py starts
intent_node on the topic input (not gesture_node, and not fake_intent, which
would fight intent_node on /gesture/intent). test_mission_node_process.py runs
the demo launch for real.

Needs the ROS 2 launch packages (ROS 2 Jazzy sourced). Without them the tests
are skipped, unless REQUIRE_ROS_TESTS=1 is set (CI sets it).

Run with:  colcon test --packages-select mission_node
or:        python3 -m unittest test.test_launch_files -v   (package folder, ROS sourced)
"""

import importlib.util
import os
import re
import unittest
from pathlib import Path

try:
    import yaml
    from launch.actions import DeclareLaunchArgument
    from launch_ros.actions import Node
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    Node = None

PACKAGE = Path(__file__).resolve().parents[1]


def entities(name):
    path = PACKAGE / "launch" / name
    spec = importlib.util.spec_from_file_location(path.stem.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_launch_description().entities


def text(value):
    """Plain text of a launch value made of TextSubstitutions."""
    if isinstance(value, (list, tuple)):
        return "".join(text(part) for part in value)
    return getattr(value, "text", value)


def param_value(value):
    """A parameter value as the node gets it: launch_ros stores YAML text."""
    value = text(value)
    return yaml.safe_load(value) if isinstance(value, str) else value


def nodes(name):
    """{(package, executable): {parameter: value}} for each Node in the file."""
    found = {}
    for entity in entities(name):
        if isinstance(entity, Node):
            params = {}
            for group in entity._Node__parameters:  # no public getter before launch
                for key, value in group.items():
                    params[text(key)] = param_value(value)
            found[(entity.node_package, entity.node_executable)] = params
    return found


def arguments(name):
    return {e.name for e in entities(name) if isinstance(e, DeclareLaunchArgument)}


def console_scripts():
    setup = (PACKAGE / "setup.py").read_text()
    return set(re.findall(r'"(\w+) = mission_node\.\w+:main"', setup))


@unittest.skipIf(Node is None, "ROS 2 launch packages not available")
class LaunchFileTests(unittest.TestCase):
    def test_setup_py_installs_three_executables(self):
        self.assertEqual(
            console_scripts(), {"mission_node", "fake_flight_state", "fake_intent"}
        )

    def test_demo_starts_the_node_and_both_pretend_inputs(self):
        self.assertEqual(
            set(nodes("mission_demo.launch.py")),
            {
                ("mission_node", "fake_flight_state"),
                ("mission_node", "mission_node"),
                ("mission_node", "fake_intent"),
            },
        )
        self.assertEqual(
            arguments("mission_demo.launch.py"), {"mask", "airborne", "loop"}
        )

    def test_gestures_starts_intent_node_on_the_topic_input(self):
        found = nodes("mission_with_gestures.launch.py")
        self.assertEqual(
            set(found),
            {
                ("intent_node", "intent_node"),
                ("mission_node", "fake_flight_state"),
                ("mission_node", "mission_node"),
            },
        )
        self.assertEqual(
            found[("intent_node", "intent_node")], {"input_source": "topic"}
        )
        self.assertEqual(
            arguments("mission_with_gestures.launch.py"), {"mask", "airborne"}
        )

    def test_every_mission_node_executable_exists(self):
        for name in ("mission_demo.launch.py", "mission_with_gestures.launch.py"):
            for package, executable in nodes(name):
                if package == "mission_node":
                    with self.subTest(launch=name, executable=executable):
                        self.assertIn(executable, console_scripts())


if __name__ == "__main__":
    unittest.main()
