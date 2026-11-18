"""The mode parameter files (config/demo|real|sim.yaml) against the nodes.

ROS ignores a parameter that a node does not declare, without a word, so a
typo in a key would silently do nothing. For each file, every node section is
a node that mode starts, and the real node, created with the file (the way
`--params-file` passes it), declares every key and gets every value.

Needs ROS 2 Jazzy sourced and the node packages installed. Without them the
tests are skipped, unless REQUIRE_ROS_TESTS=1 is set (CI sets it).

Run with:  colcon test --packages-select ground_bringup
or:        python3 -m pytest test -v   (package folder, ROS sourced)
"""

import importlib
import os
import unittest
from pathlib import Path

import yaml

try:
    import rclpy
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

PACKAGE = Path(__file__).resolve().parents[1]

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them)
TEST_DOMAIN_ID = 97

# node name -> (module, class)
NODES = {
    "gesture_node": ("gesture_node.gesture_node", "GestureNode"),
    "intent_node": ("intent_node.intent_node", "IntentNode"),
    "mission_node": ("mission_node.mission_node", "MissionNode"),
    "fake_flight_state": ("mission_node.fake_flight_state", "FakeFlightState"),
    "flight_interface": ("flight_interface.link_node", "FlightInterface"),
}
# The nodes each mode's launch file starts (test_mode_launch_files.py checks those)
MODE_NODES = {
    "demo": {"gesture_node", "intent_node", "mission_node", "fake_flight_state"},
    "real": {"gesture_node", "intent_node", "mission_node", "flight_interface"},
    "sim": {"gesture_node", "intent_node", "mission_node"},
}


def config(mode):
    return yaml.safe_load((PACKAGE / "config" / f"{mode}.yaml").read_text())


def node_class(name):
    """The node's class, or None if its package is not installed here."""
    module, cls = NODES[name]
    try:
        return getattr(importlib.import_module(module), cls)
    except ImportError:
        if os.environ.get("REQUIRE_ROS_TESTS"):
            raise
        return None


class ConfigFileTests(unittest.TestCase):
    def test_sections_are_nodes_the_mode_starts(self):
        for mode, started in MODE_NODES.items():
            with self.subTest(mode=mode):
                data = config(mode)
                self.assertIsInstance(data, dict)
                self.assertLessEqual(set(data), started)
                for name, section in data.items():
                    self.assertEqual(set(section), {"ros__parameters"}, name)
                    self.assertIsInstance(section["ros__parameters"], dict, name)

    def test_intent_node_reads_gesture_node_topic(self):
        for mode in MODE_NODES:
            with self.subTest(mode=mode):
                params = config(mode)["intent_node"]["ros__parameters"]
                self.assertEqual(params["input_source"], "topic")

    def test_real_never_uses_the_loopback(self):
        params = config("real")["flight_interface"]["ros__parameters"]
        self.assertEqual(params["backend"], "esp")


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class NodesDeclareEveryKeyTests(unittest.TestCase):
    def check(self, mode):
        path = PACKAGE / "config" / f"{mode}.yaml"
        rclpy.init(
            args=["--ros-args", "--params-file", str(path)], domain_id=TEST_DOMAIN_ID
        )
        try:
            for name, section in config(mode).items():
                cls = node_class(name)
                if cls is None:
                    self.skipTest(f"{name} not installed")
                with self.subTest(mode=mode, node=name):
                    node = cls()
                    try:
                        for key, value in section["ros__parameters"].items():
                            self.assertTrue(
                                node.has_parameter(key),
                                f"{name} does not declare {key}",
                            )
                            self.assertEqual(node.get_parameter(key).value, value, key)
                    finally:
                        node.destroy_node()
        finally:
            rclpy.shutdown()

    def test_demo(self):
        self.check("demo")

    def test_real(self):
        self.check("real")

    def test_sim(self):
        self.check("sim")


if __name__ == "__main__":
    unittest.main()
