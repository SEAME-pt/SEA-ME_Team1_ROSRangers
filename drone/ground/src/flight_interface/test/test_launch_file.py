"""Decision A6: the launch file never respawns flight_interface (the E-stop latch
lives in the process). Static check, no ROS needed."""

import ast
import pathlib

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
