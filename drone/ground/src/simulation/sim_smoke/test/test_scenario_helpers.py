"""Unit tests of the scenario helpers (smoke_scenario.py, hold_scenario.py)
with every ROS, PX4 and Gazebo import stubbed: they run on any machine, as
in CI's sim-smoke-python job. The scenarios themselves need PX4 SITL
(make sim-smoke, make sim-hold)."""

import importlib
import math
import sys
import threading
import types

import pytest

STUBBED = (
    "rclpy",
    "rclpy.executors",
    "rclpy.node",
    "rclpy.qos",
    "rclpy.parameter",
    "rclpy.parameter_client",
    "ament_index_python",
    "ament_index_python.packages",
    "px4_msgs",
    "px4_msgs.msg",
    "geometry_msgs",
    "geometry_msgs.msg",
    "std_msgs",
    "std_msgs.msg",
    "gz",
    "gz.msgs10",
    "gz.msgs10.pose_v_pb2",
    "gz.transport13",
)


class _Anything(types.ModuleType):
    """A module whose every attribute is a dummy class."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {"__init__": lambda self, *a, **k: None})


@pytest.fixture
def scenarios(monkeypatch):
    for name in STUBBED:
        monkeypatch.setitem(sys.modules, name, _Anything(name))
    try:
        import yaml  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "yaml", _Anything("yaml"))
    for name in ("sim_smoke.smoke_scenario", "sim_smoke.hold_scenario"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    smoke = importlib.import_module("sim_smoke.smoke_scenario")
    hold = importlib.import_module("sim_smoke.hold_scenario")
    yield types.SimpleNamespace(smoke=smoke, hold=hold)
    for name in ("sim_smoke.smoke_scenario", "sim_smoke.hold_scenario"):
        sys.modules.pop(name, None)


def test_window_keeps_samples_from_t0(scenarios):
    samples = [(1.0, "a"), (2.0, "b"), (3.0, "c")]
    assert scenarios.smoke.window(samples, 2.0) == ["b", "c"]
    assert scenarios.smoke.window(samples, 9.0) == []


def test_hover_window_covers_at_least_3_s(scenarios):
    # REQ-SAF-017 hover judged over >= 3 s (TEST_REPORT 2.B.7)
    assert scenarios.smoke.HOVER_WINDOW_SEC >= 3.0


def test_hover_throttle_reads_the_controllers(scenarios, tmp_path):
    pytest.importorskip("yaml")
    path = tmp_path / "sim.yaml"
    path.write_text("controller_node:\n  ros__parameters:\n    hover_throttle: 0.73\n")
    assert scenarios.smoke.hover_throttle(str(path)) == 0.73
    path.write_text("controller_node:\n  ros__parameters: {}\n")
    with pytest.raises(KeyError):
        scenarios.smoke.hover_throttle(str(path))


@pytest.mark.parametrize(
    "q, yaw",
    [
        ((1.0, 0.0, 0.0, 0.0), 0.0),
        ((math.cos(math.pi / 4), 0.0, 0.0, math.sin(math.pi / 4)), math.pi / 2),
        ((0.0, 0.0, 0.0, 1.0), math.pi),
    ],
)
def test_yaw_from_quaternion(scenarios, q, yaw):
    got = scenarios.hold.yaw_from_quaternion(*q)
    assert math.cos(got - yaw) == pytest.approx(1.0)


def test_between_and_values_between(scenarios):
    recorder = types.SimpleNamespace(lock=threading.Lock())
    between = scenarios.hold.Recorder.between
    values_between = scenarios.hold.Recorder.values_between
    samples = [(1.0, "a"), (2.0, "b"), (3.0, "c")]
    assert between(recorder, samples, 1.5, 3.0) == [(2.0, "b"), (3.0, "c")]
    recorder.between = lambda s, t0, t1: between(recorder, s, t0, t1)
    assert values_between(recorder, samples, 0.0, 2.0) == ["a", "b"]


class _Future:
    def __init__(self, ready_after):
        self._calls = 0
        self._ready_after = ready_after

    def done(self):
        self._calls += 1
        return self._calls > self._ready_after

    def result(self):
        return "reply"


def test_wait_future(scenarios):
    assert scenarios.hold.wait_future(_Future(ready_after=2), timeout=1.0) == "reply"
    assert scenarios.hold.wait_future(_Future(ready_after=10**9), timeout=0.05) is None


def _param_node(hover=0.73, services=True, set_ok=True):
    """A recorder with a fake AsyncParameterClient that answers at once."""

    class Done:
        def __init__(self, value):
            self.value = value

        def done(self):
            return True

        def result(self):
            return self.value

    sets = []

    def set_parameters(parameters):
        sets.append(parameters)
        result = types.SimpleNamespace(successful=set_ok, reason="" if set_ok else "refused")
        return Done(types.SimpleNamespace(results=[result]))

    params = types.SimpleNamespace(
        wait_for_services=lambda timeout_sec: services,
        get_parameters=lambda names: Done(
            types.SimpleNamespace(values=[types.SimpleNamespace(double_value=hover)])
        ),
        set_parameters=set_parameters,
    )
    return types.SimpleNamespace(params=params, sets=sets)


def test_controller_hover_and_set(scenarios, monkeypatch):
    hold = scenarios.hold
    monkeypatch.setattr(hold, "Parameter", lambda name, value: (name, value))
    node = _param_node()
    assert hold.controller_hover(node) == 0.73
    assert hold.set_controller_hover(node, 0.0) == (True, "")
    assert node.sets == [[("hover_throttle", 0.0)]]
    assert hold.controller_hover(_param_node(services=False)) is None
    assert hold.set_controller_hover(_param_node(set_ok=False), 0.0) == (False, "refused")


def test_neutral_phase_refuses_without_a_controller(scenarios):
    # No answer (or a controller already paused): fail at once, pause nothing
    for node in (_param_node(services=False), _param_node(hover=0.0)):
        checks, notes = scenarios.hold.neutral_phase(node)
        assert [c.ok for c in checks] == [False] and notes == []
        assert node.sets == []
