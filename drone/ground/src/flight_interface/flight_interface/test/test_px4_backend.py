"""Tests of px4_backend.py (#199) with a fake node and fake PX4 message
classes: CI and the normal images have no px4_msgs (ADR-025 F3). The frames
and the yaw are tested in test_px4_logic.py; here, what goes out on which
topic, the state for the guard, /estimate/velocity and what it never does.

Needs rclpy and geometry_msgs (px4_backend.py imports them); skipped without
them unless REQUIRE_ROS_TESTS=1 is set (CI sets it).
"""

import math
import os
import types

import pytest

try:
    from flight_interface import px4_backend
    from flight_interface.px4_backend import (
        ATTITUDE_SETPOINT_TOPIC,
        LAND_TOPIC,
        OFFBOARD_MODE_TOPIC,
        POSITION_TOPIC,
        STATUS_TOPIC,
        TRAJECTORY_SETPOINT_TOPIC,
        VELOCITY_TOPIC,
        Px4Backend,
    )
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    pytest.skip("rclpy / geometry_msgs not available", allow_module_level=True)

from flight_interface.backends import make_backend
from flight_interface.guard import NEUTRAL, Setpoint

HOVER = 0.7
EAST = math.pi / 2


class Msg:
    """A message: any attribute can be set, like a ROS message."""


def fake_msgs():
    m = types.SimpleNamespace()
    for name in (
        "OffboardControlMode",
        "VehicleAttitudeSetpoint",
        "TrajectorySetpoint",
        "VehicleLocalPosition",
        "VehicleLandDetected",
    ):
        setattr(m, name, type(name, (Msg,), {}))
    m.VehicleStatus = type(
        "VehicleStatus",
        (Msg,),
        {"ARMING_STATE_ARMED": 2, "NAVIGATION_STATE_OFFBOARD": 14},
    )
    return m


class FakePub:
    def __init__(self, topic):
        self.topic = topic
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg)


class FakeNode:
    def __init__(self):
        self.pubs = {}
        self.subs = {}
        self.destroyed = []
        self.warnings = []
        clock = types.SimpleNamespace(
            now=lambda: types.SimpleNamespace(
                nanoseconds=5_000_000, to_msg=lambda: "stamp"
            )
        )
        self.get_clock = lambda: clock
        self.get_logger = lambda: types.SimpleNamespace(warning=self.warnings.append)

    def create_publisher(self, msg_type, topic, qos):
        pub = FakePub(topic)
        self.pubs[topic] = pub
        return pub

    def create_subscription(self, msg_type, topic, callback, qos):
        self.subs[topic] = callback
        return topic

    def destroy_publisher(self, pub):
        self.destroyed.append(pub.topic)

    def destroy_subscription(self, sub):
        self.destroyed.append(sub)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def rig():
    node = FakeNode()
    clock = Clock()
    msgs = fake_msgs()
    backend = Px4Backend(node, 15, HOVER, msgs=msgs, clock=clock)
    return types.SimpleNamespace(node=node, clock=clock, msgs=msgs, backend=backend)


def feed_px4(
    rig,
    armed=True,
    offboard=True,
    heading=0.0,
    v=(0.0, 0.0, 0.0),
    valid=True,
    z_deriv=None,
):
    """z_deriv: the rate of PX4's height estimate; by default the same as vz."""
    status = rig.msgs.VehicleStatus()
    status.arming_state = 2 if armed else 1
    status.nav_state = 14 if offboard else 4
    rig.node.subs[STATUS_TOPIC](status)
    pos = rig.msgs.VehicleLocalPosition()
    pos.heading = heading
    pos.heading_good_for_control = True
    pos.vx, pos.vy, pos.vz = v
    if z_deriv is None:
        z_deriv = v[2]
    pos.z_deriv = z_deriv
    pos.v_xy_valid = valid
    pos.v_z_valid = valid
    pos.z_valid = valid
    rig.node.subs[POSITION_TOPIC](pos)


def sent(rig, topic):
    return rig.node.pubs[topic].sent


# ---------- out ----------
def test_a_commanding_setpoint_goes_out_as_attitude(rig):
    feed_px4(rig)
    assert rig.backend.send_setpoint(Setpoint(0.0, 5.0, 0.0, 0.72, True), 0.0) is True
    (mode,) = sent(rig, OFFBOARD_MODE_TOPIC)
    assert (mode.attitude, mode.velocity, mode.position) == (True, False, False)
    assert mode.timestamp == 5000  # µs from the node clock
    (sp,) = sent(rig, ATTITUDE_SETPOINT_TOPIC)
    assert sp.thrust_body == pytest.approx([0.0, 0.0, -0.72])
    assert len(sp.q_d) == 4 and sp.yaw_sp_move_rate == 0.0
    assert sent(rig, TRAJECTORY_SETPOINT_TOPIC) == []


def test_neutral_is_px4s_zero_velocity_hold(rig):
    # F19: active false -> velocity Offboard, zero velocity: PX4 holds itself.
    feed_px4(rig)
    for command in (Setpoint(10.0, 10.0, 30.0, 0.9, False), NEUTRAL):
        assert rig.backend.send_setpoint(command, 0.0) is True
    assert rig.backend.send_neutral(0.0) is True
    modes = sent(rig, OFFBOARD_MODE_TOPIC)
    assert [(m.attitude, m.velocity) for m in modes] == [(False, True)] * 3
    for sp in sent(rig, TRAJECTORY_SETPOINT_TOPIC):
        assert sp.velocity == [0.0, 0.0, 0.0] and sp.yawspeed == 0.0
        assert all(math.isnan(v) for v in sp.position)
        assert math.isnan(sp.yaw)
    assert sent(rig, ATTITUDE_SETPOINT_TOPIC) == []


def test_no_heading_yet_gives_the_hold_not_an_attitude(rig):
    assert rig.backend.send_setpoint(Setpoint(0.0, 5.0, 0.0, HOVER, True), 0.0) is True
    assert sent(rig, ATTITUDE_SETPOINT_TOPIC) == []
    assert len(sent(rig, TRAJECTORY_SETPOINT_TOPIC)) == 1


# ---------- in ----------
def test_poll_gives_the_state_while_px4_is_fresh(rig):
    assert rig.backend.poll(0.0) == []  # nothing from PX4 yet
    feed_px4(rig)
    (state,) = rig.backend.poll(0.1)
    assert state["esp_state"] == "STREAMING" and state["override_active"] is True
    assert state["mask"] == 15 and state["fc_link_ok"] is True
    assert rig.backend.poll(5.0) == []  # stale: the guard sees the link lost


def test_override_off_until_armed_and_offboard(rig):
    feed_px4(rig, offboard=False)
    assert rig.backend.poll(0.0)[0]["override_active"] is False


def test_land_detector_gives_airborne(rig):
    feed_px4(rig)
    landed = rig.msgs.VehicleLandDetected()
    landed.landed = False
    rig.node.subs[LAND_TOPIC](landed)
    assert rig.backend.poll(0.0)[0]["airborne"] is True


def test_measured_velocity_published_in_body_flu(rig):
    # Facing east, moving east at 0.4 m/s and climbing at 0.1 m/s (NED down -0.1).
    feed_px4(rig, heading=EAST, v=(0.0, 0.4, -0.1))
    (out,) = sent(rig, VELOCITY_TOPIC)
    assert out.twist.linear.x == pytest.approx(0.4)
    assert out.twist.linear.y == pytest.approx(0.0, abs=1e-9)
    assert out.twist.linear.z == pytest.approx(0.1)
    assert out.header.frame_id == "base_link"


def test_vertical_velocity_is_the_rate_of_px4s_height(rig):
    # SITL: vz said 0 while z rose 0.1 m/s; the controller must see the rise
    feed_px4(rig, v=(0.0, 0.0, 0.0), z_deriv=-0.1)
    (out,) = sent(rig, VELOCITY_TOPIC)
    assert out.twist.linear.z == pytest.approx(0.1)


def test_no_measured_velocity_when_px4_calls_it_invalid(rig):
    feed_px4(rig, valid=False)
    feed_px4(rig, v=(math.nan, 0.0, 0.0))
    feed_px4(rig, z_deriv=math.nan)
    assert sent(rig, VELOCITY_TOPIC) == []


# ---------- never arms, never changes PX4's mode (REQ-SAF-023) ----------
def test_arm_and_resume_send_nothing(rig):
    feed_px4(rig)
    assert rig.backend.arm_control(15, 0.0, None) is False
    assert rig.backend.request_resume(0.0, None) is False
    assert len(rig.node.warnings) == 2
    assert rig.backend.end_session(0.0) is True
    every_topic = set(rig.node.pubs)
    assert every_topic == {
        OFFBOARD_MODE_TOPIC,
        ATTITUDE_SETPOINT_TOPIC,
        TRAJECTORY_SETPOINT_TOPIC,
        VELOCITY_TOPIC,
    }  # no vehicle_command: nothing that could arm or change the mode
    assert all(sent(rig, t) == [] for t in every_topic - {VELOCITY_TOPIC})


def test_close_destroys_everything(rig):
    rig.backend.close()
    assert set(rig.node.destroyed) == set(rig.node.pubs) | set(rig.node.subs)


# ---------- construction ----------
@pytest.mark.parametrize("hover", [0.0, -0.5, None, "0.7"])
def test_refuses_without_a_measured_hover_throttle(hover):
    node = FakeNode()
    with pytest.raises(ValueError):
        Px4Backend(node, 15, hover, msgs=fake_msgs())
    assert node.pubs == {}  # nothing created


def test_make_backend_px4_without_px4_msgs_is_an_import_error(monkeypatch):
    def no_px4_msgs():
        raise ImportError("No module named 'px4_msgs'")

    monkeypatch.setattr(px4_backend, "load_px4_msgs", no_px4_msgs)
    with pytest.raises(ImportError):
        make_backend("px4", node=FakeNode(), mask=15, hover_throttle=HOVER)


def test_make_backend_px4_passes_node_mask_and_hover(monkeypatch):
    monkeypatch.setattr(px4_backend, "load_px4_msgs", fake_msgs)
    node = FakeNode()
    backend = make_backend("px4", node=node, mask=11, hover_throttle=HOVER)
    assert backend.name == "px4" and backend.logic.mask == 11
    assert STATUS_TOPIC in node.subs
