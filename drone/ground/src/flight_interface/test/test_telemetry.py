"""Unit tests for telemetry.py: MSP replies -> /fc/* fields. No ROS, no drone.

The replay tests read FC replies from a `msp_check --trace` log in test/data
and run them through the same decode + conversion as the node.

Run with:  python3 -m pytest test/test_telemetry.py   (package folder)
"""

import math
import struct

import pytest
from fake_fc import FakeFc, load_trace

from flight_interface import msp
from flight_interface.telemetry import (
    POWER_SUPPLY_TECHNOLOGY_LIPO,
    AttitudeConverter,
    battery_fields,
    midpoint_ns,
    wrap_pi,
)

DEG = math.pi / 180


def attitude(roll=0.0, pitch=0.0, yaw=0):
    return {"roll_deg": roll, "pitch_deg": pitch, "yaw_deg": yaw}


@pytest.mark.parametrize(
    "angle,expected",
    [
        (0.0, 0.0),
        (math.pi, math.pi),
        (-math.pi, math.pi),  # (-pi, pi]: -pi becomes pi
        (3 * math.pi / 2, -math.pi / 2),
        (-3 * math.pi / 2, math.pi / 2),
        (4 * math.pi + 0.1, 0.1),
    ],
)
def test_wrap_pi(angle, expected):
    assert wrap_pi(angle) == pytest.approx(expected)


def test_midpoint_is_halfway_between_request_and_reply():
    assert midpoint_ns(1_000_000_000, 1_020_000_000) == 1_010_000_000
    assert midpoint_ns(5, 5) == 5


def test_level_drone_is_all_zero():
    assert AttitudeConverter().convert(attitude(yaw=123)) == (0.0, 0.0, 0.0)


def test_roll_and_pitch_to_rep103_radians():
    # Assumed Betaflight signs (bench check M1 pending): roll + right side
    # down (= REP-103), pitch + nose up (REP-103: + nose down).
    roll, pitch, _ = AttitudeConverter().convert(attitude(roll=10.0, pitch=5.0))
    assert roll == pytest.approx(10 * DEG)
    assert pitch == pytest.approx(-5 * DEG)


def test_yaw_is_relative_to_the_first_sample_and_counter_clockwise():
    conv = AttitudeConverter()
    assert conv.convert(attitude(yaw=90))[2] == 0.0
    # Betaflight yaw + clockwise (measured 2026-11-02): turning 30 deg
    # clockwise is -30 deg in REP-103.
    assert conv.convert(attitude(yaw=120))[2] == pytest.approx(-30 * DEG)
    assert conv.convert(attitude(yaw=60))[2] == pytest.approx(30 * DEG)


def test_yaw_wraps_across_north():
    conv = AttitudeConverter()
    conv.convert(attitude(yaw=350))
    assert conv.convert(attitude(yaw=10))[2] == pytest.approx(-20 * DEG)
    assert conv.convert(attitude(yaw=170))[2] == pytest.approx(180 * DEG)


def test_reset_yaw_takes_a_new_zero():
    conv = AttitudeConverter()
    conv.convert(attitude(yaw=10))
    conv.reset_yaw()
    assert conv.convert(attitude(yaw=200))[2] == 0.0


def test_battery_prefers_the_centivolt_field():
    fields = battery_fields({"vbat_v": 15.9, "voltage_v": 15.87}, cell_count=4)
    assert fields["voltage"] == 15.87


def test_battery_falls_back_to_the_legacy_byte():
    assert battery_fields({"vbat_v": 15.9}, cell_count=4)["voltage"] == 15.9


def test_battery_other_fields_are_unknown():
    fields = battery_fields({"vbat_v": 15.9}, cell_count=4)
    for name in ("temperature", "current", "charge", "capacity", "percentage"):
        assert math.isnan(fields[name]), name
    assert math.isnan(fields["design_capacity"])
    assert fields["power_supply_technology"] == POWER_SUPPLY_TECHNOLOGY_LIPO
    assert fields["power_supply_status"] == 0 and fields["power_supply_health"] == 0
    assert fields["present"] is True
    assert len(fields["cell_voltage"]) == 4
    assert all(math.isnan(v) for v in fields["cell_voltage"])
    assert len(fields["cell_temperature"]) == 4


def test_msp_analog_decodes_the_centivolt_field():
    payload = struct.pack("<BHHhH", 159, 120, 914, 250, 1587)
    decoded = msp.decode(msp.MSP_ANALOG, payload)
    assert decoded == {"vbat_v": 15.9, "rssi": 914, "voltage_v": 15.87}


def test_trace_replay_through_the_client():
    answers = load_trace("msp_trace_synthetic.txt")
    assert len(answers[msp.MSP_ATTITUDE]) == 2
    client = msp.Client(FakeFc(answers))
    conv = AttitudeConverter()

    frame, _ = client.request(msp.MSP_ATTITUDE, timeout=0.05)
    roll, pitch, yaw = conv.convert(msp.decode(frame.cmd, frame.payload))
    assert (roll, pitch, yaw) == pytest.approx((-0.6 * DEG, 0.3 * DEG, 0.0))

    frame, _ = client.request(msp.MSP_ANALOG, timeout=0.05)
    fields = battery_fields(msp.decode(frame.cmd, frame.payload), cell_count=4)
    assert fields["voltage"] == 15.9

    frame, _ = client.request(msp.MSP_ATTITUDE, timeout=0.05)
    roll, pitch, yaw = conv.convert(msp.decode(frame.cmd, frame.payload))
    # Yaw 358 -> 81 deg: 83 deg clockwise, so -83 deg in REP-103
    assert (roll, pitch, yaw) == pytest.approx((171.3 * DEG, -9.2 * DEG, -83 * DEG))

    frame, _ = client.request(msp.MSP_ANALOG, timeout=0.05)
    assert msp.decode(frame.cmd, frame.payload)["voltage_v"] == 15.5

    # The trace ends with an attitude request the FC never answered
    assert client.request(msp.MSP_ATTITUDE, timeout=0.01) == (None, None)
