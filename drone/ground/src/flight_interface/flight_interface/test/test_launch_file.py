"""Decision A6: the launch file never respawns flight_interface (the E-stop latch
lives in the process). Static check, no ROS needed.

Decision E1: the launch file keeps ROS on this PC (team domain 42, localhost
discovery) unless the caller set the variables. Evaluated with the launch
library when it is installed (ROS 2 sourced)."""

import ast
import importlib.util
import pathlib

import pytest

LAUNCH = pathlib.Path(__file__).resolve().parents[1] / "launch"


def test_no_launch_file_respawns_the_node():
    files = sorted(LAUNCH.glob("*.launch.py"))
    assert files
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.keyword) and node.arg in (
                "respawn",
                "respawn_delay",
            ):
                raise AssertionError(f"{path.name}:{node.value.lineno} sets {node.arg}")


def test_the_launch_file_leaves_the_node_unconfigured():
    text = (LAUNCH / "flight_interface.launch.py").read_text()
    tree = ast.parse(text)
    calls = {
        getattr(n.func, "id", getattr(n.func, "attr", None))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    # No lifecycle events in the launch file: configure/activate stay manual.
    assert not calls & {"ChangeState", "EmitEvent", "LifecycleNode"}


def launch_description():
    path = LAUNCH / "flight_interface.launch.py"
    spec = importlib.util.spec_from_file_location("flight_interface_launch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_launch_description()


@pytest.mark.parametrize(
    "preset, expected",
    [
        ({}, {"ROS_DOMAIN_ID": "42", "ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"}),
        (
            {"ROS_DOMAIN_ID": "96", "ROS_AUTOMATIC_DISCOVERY_RANGE": "SUBNET"},
            {"ROS_DOMAIN_ID": "96", "ROS_AUTOMATIC_DISCOVERY_RANGE": "SUBNET"},
        ),
        (
            {"ROS_DOMAIN_ID": "7"},
            {"ROS_DOMAIN_ID": "7", "ROS_AUTOMATIC_DISCOVERY_RANGE": "LOCALHOST"},
        ),
    ],
    ids=["defaults", "caller-wins", "mixed"],
)
def test_ros_stays_on_this_pc_unless_the_caller_says_otherwise(
    monkeypatch, preset, expected
):
    pytest.importorskip("launch")
    from launch import LaunchContext
    from launch.actions import SetEnvironmentVariable

    for name in expected:
        monkeypatch.delenv(name, raising=False)
    for name, value in preset.items():
        monkeypatch.setenv(name, value)
    context = LaunchContext()
    actions = [
        e
        for e in launch_description().entities
        if isinstance(e, SetEnvironmentVariable)
    ]
    assert len(actions) == 2
    for action in actions:
        action.visit(context)
    for name, value in expected.items():
        assert context.environment[name] == value
