"""The mode parameter files (config/demo|real|sim.yaml) against the nodes.

ROS ignores a parameter that a node does not declare, without a word, so a
typo in a key would silently do nothing. For each file, every node section is
a node that mode starts, and the real node, created with the file (the way
`--params-file` passes it), declares every key and gets every value.

Values several nodes share (SHARED_KEYS: mask, hover_throttle; ADR-025 F15)
are repeated in each node's section, and every started node that declares
one must end up with the same value. That is checked on the real nodes, so a
forgotten copy, which would fall back to the node's default, fails too.

No `/**:` (wildcard) section: with one in the file, a `-p` on the command line
no longer overrides a key in a node's own section, so `make real-dry`'s
`-p backend:=loopback` would lose to real.yaml's `backend: esp` (found on
2026-11-23, #199). test_real_dry_override_wins guards that.

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
TEST_DOMAIN_ID = 79

# node name -> (module, class)
NODES = {
    "gesture_node": ("gesture_node.gesture_node", "GestureNode"),
    "intent_node": ("intent_node.intent_node", "IntentNode"),
    "mission_node": ("mission_node.mission_node", "MissionNode"),
    "controller_node": ("controller_node.controller_node", "ControllerNode"),
    "fake_flight_state": ("mission_node.fake_flight_state", "FakeFlightState"),
    "flight_interface": ("flight_interface.link_node", "FlightInterface"),
}
# The nodes each mode starts: its launch file's (test_mode_launch_files.py
# checks those), plus, in sim, flight_interface, which only make sim starts
# (its simulation image has px4_msgs, #199). A mode file's sections must be
# among them, and the shared keys must agree across them (F15).
MODE_NODES = {
    "demo": {"gesture_node", "intent_node", "mission_node", "fake_flight_state"},
    "real": {
        "gesture_node",
        "intent_node",
        "mission_node",
        "controller_node",
        "flight_interface",
    },
    "sim": {
        "gesture_node",
        "intent_node",
        "mission_node",
        "controller_node",
        "flight_interface",
    },
}


# Values several nodes of a mode share; they must agree (F15).
SHARED_KEYS = ("mask", "hover_throttle")


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

    def test_no_wildcard_section(self):
        # A /** section breaks `-p` overrides of node keys (module docstring).
        for mode in MODE_NODES:
            with self.subTest(mode=mode):
                wildcard = [name for name in config(mode) if name.startswith("/")]
                self.assertEqual(wildcard, [])

    def test_real_never_uses_the_loopback(self):
        params = config("real")["flight_interface"]["ros__parameters"]
        self.assertEqual(params["backend"], "esp")

    def test_sim_flies_px4_with_a_measured_hover_throttle(self):
        # F3, F16, F20: the px4 backend refuses 0.0, and the controller stays
        # inactive with it, so the simulation would never fly.
        data = config("sim")
        self.assertEqual(data["flight_interface"]["ros__parameters"]["backend"], "px4")
        for name in ("controller_node", "flight_interface"):
            with self.subTest(node=name):
                hover = data[name]["ros__parameters"]["hover_throttle"]
                self.assertGreater(hover, 0.0)


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class NodesDeclareEveryKeyTests(unittest.TestCase):
    def check(self, mode):
        """Every node section of the mode file, on the real node. A node
        whose package is not installed here is left out, and the others are
        still checked; the test is reported as skipped at the end if any
        was left out (PR #253 review). With REQUIRE_ROS_TESTS=1 (CI) a
        missing package fails in node_class instead."""
        path = PACKAGE / "config" / f"{mode}.yaml"
        rclpy.init(
            args=["--ros-args", "--params-file", str(path)], domain_id=TEST_DOMAIN_ID
        )
        missing = set()
        try:
            data = config(mode)
            for name, section in data.items():
                cls = node_class(name)
                if cls is None:
                    missing.add(name)
                    continue
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
            missing.update(self.check_shared(mode))
        finally:
            rclpy.shutdown()
        if missing:
            self.skipTest(f"not installed, not checked: {', '.join(sorted(missing))}")

    def check_shared(self, mode):
        """F15: every node the mode starts that declares a SHARED_KEYS key
        has the same value for it (from the file, or its default if the
        file forgot it there). Compares the nodes that are installed here;
        returns the names of those that are not."""
        missing = set()
        values = {key: {} for key in SHARED_KEYS}
        for name in sorted(MODE_NODES[mode]):
            cls = node_class(name)
            if cls is None:
                missing.add(name)
                continue
            node = cls()
            try:
                for key in SHARED_KEYS:
                    if node.has_parameter(key):
                        values[key][name] = node.get_parameter(key).value
            finally:
                node.destroy_node()
        for key, by_node in values.items():
            with self.subTest(mode=mode, key=key):
                self.assertLessEqual(len(set(by_node.values())), 1, by_node)
        return missing

    def test_real_dry_override_wins(self):
        # make real-dry: real.yaml, then -p backend:=loopback. The override
        # must win, or the dry run would reach for the drone.
        cls = node_class("flight_interface")
        if cls is None:
            self.skipTest("flight_interface not installed")
        path = PACKAGE / "config" / "real.yaml"
        rclpy.init(
            args=["--ros-args", "--params-file", str(path), "-p", "backend:=loopback"],
            domain_id=TEST_DOMAIN_ID,
        )
        try:
            node = cls()
            try:
                self.assertEqual(node.get_parameter("backend").value, "loopback")
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
