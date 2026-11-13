"""Unit tests for telemetry.py: MSP replies -> /fc/* fields. No ROS, no drone.

The replay tests read FC replies from a `msp_check --trace` log in test/data
and run them through the same decode + conversion as the node. The D2 tests
send the same replies through STATE, as the gateway does, and check that the
flight_interface path gives the same /fc/* values as the fc_telemetry path.

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
    state_analog,
    state_attitude,
    wrap_pi,
)

try:
    from sd_protocol.messages import EspState, State, decode_state, encode_state
except ImportError:  # no sd_protocol: the STATE wire round trip is skipped
    State = None

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
    # Betaflight signs, measured 2026-11-06: roll + right side down, pitch
    # + nose down. Both equal REP-103.
    roll, pitch, _ = AttitudeConverter().convert(attitude(roll=10.0, pitch=5.0))
    assert roll == pytest.approx(10 * DEG)
    assert pitch == pytest.approx(5 * DEG)


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


@pytest.mark.parametrize("volts", [0.0, 0.4])
def test_no_battery_is_not_present_and_nan(volts):
    # FC on USB power only: Betaflight reports about 0 V
    fields = battery_fields({"vbat_v": volts, "voltage_v": volts}, cell_count=4)
    assert fields["present"] is False
    assert math.isnan(fields["voltage"])
    assert len(fields["cell_voltage"]) == 4


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
    assert (roll, pitch, yaw) == pytest.approx((-0.6 * DEG, -0.3 * DEG, 0.0))

    frame, _ = client.request(msp.MSP_ANALOG, timeout=0.05)
    fields = battery_fields(msp.decode(frame.cmd, frame.payload), cell_count=4)
    assert fields["voltage"] == 15.9

    frame, _ = client.request(msp.MSP_ATTITUDE, timeout=0.05)
    roll, pitch, yaw = conv.convert(msp.decode(frame.cmd, frame.payload))
    # Yaw 358 -> 81 deg: 83 deg clockwise, so -83 deg in REP-103
    assert (roll, pitch, yaw) == pytest.approx((171.3 * DEG, 9.2 * DEG, -83 * DEG))

    frame, _ = client.request(msp.MSP_ANALOG, timeout=0.05)
    assert msp.decode(frame.cmd, frame.payload)["voltage_v"] == 15.5

    # The trace ends with an attitude request the FC never answered
    assert client.request(msp.MSP_ATTITUDE, timeout=0.01) == (None, None)


def test_real_bridge_trace_replay():
    """Real capture through the ESP bridge (2026-11-06, Betaflight 2026.6.2).

    Two `msp_check info` runs: drone level, then turned ~40 deg clockwise and
    held with the right side and the nose down.
    """
    answers = load_trace("msp_trace_bench_udp.txt")
    assert len(answers[msp.MSP_ATTITUDE]) == 2
    assert len(answers[msp.MSP_ANALOG]) == 2
    client = msp.Client(FakeFc(answers))
    conv = AttitudeConverter()

    # Level: raw roll -0.6, pitch 0.4, yaw 17 (the yaw zero)
    frame, _ = client.request(msp.MSP_ATTITUDE, timeout=0.05)
    roll, pitch, yaw = conv.convert(msp.decode(frame.cmd, frame.payload))
    assert (roll, pitch, yaw) == pytest.approx((-0.6 * DEG, 0.4 * DEG, 0.0))

    frame, _ = client.request(msp.MSP_ANALOG, timeout=0.05)
    fields = battery_fields(msp.decode(frame.cmd, frame.payload), cell_count=4)
    assert fields["voltage"] == pytest.approx(15.53)
    assert fields["present"]

    # Tilted: raw roll 18.7, pitch 21.7, yaw 55. Right side down and nose
    # down are both positive in REP-103; 38 deg clockwise is -38 deg.
    frame, _ = client.request(msp.MSP_ATTITUDE, timeout=0.05)
    roll, pitch, yaw = conv.convert(msp.decode(frame.cmd, frame.payload))
    assert (roll, pitch, yaw) == pytest.approx((18.7 * DEG, 21.7 * DEG, -38 * DEG))

    frame, _ = client.request(msp.MSP_ANALOG, timeout=0.05)
    fields = battery_fields(msp.decode(frame.cmd, frame.payload), cell_count=4)
    assert fields["voltage"] == pytest.approx(15.52)


# ---------- D2: the same values through STATE ----------
def state_from_fc(attitude_payload, analog_payload):
    """The FC values of a STATE as the gateway builds it from these MSP
    replies, sent through the real STATE encoding. sd::msp::decode_attitude
    reads roll and pitch as i16 and the heading as u16; decode_analog takes
    the u16 voltage at offset 7 and refuses a reply under 9 bytes (then
    has_analog stays false and fc_view() sends vbat 0)."""
    roll, pitch, heading = struct.unpack_from("<hhH", attitude_payload)
    vbat = (
        struct.unpack_from("<H", analog_payload, 7)[0]
        if len(analog_payload) >= 9
        else 0
    )
    msg = State(
        esp_state=int(EspState.STREAMING),
        override_active=1,
        last_ctrl_seq=0,
        last_ctrl_t_send_us=0,
        roll=roll,
        pitch=pitch,
        heading=heading,
        vbat=vbat,
        flow_x=0,
        flow_y=0,
        flow_quality=0,
        flow_status=4,
        rx_rejected=0,
        msp_errors=0,
    )
    wire = decode_state(encode_state(msg))
    return {
        "roll": wire.roll,
        "pitch": wire.pitch,
        "heading": wire.heading,
        "vbat": wire.vbat,
    }


def test_state_values_in_msp_units():
    values = {"roll": -6, "pitch": 4, "heading": 17, "vbat": 1553}
    assert state_attitude(values) == {"roll_deg": -0.6, "pitch_deg": 0.4, "yaw_deg": 17}
    assert state_analog(values) == {"voltage_v": 15.53}
    assert battery_fields(state_analog({"vbat": 0}), cell_count=4)["present"] is False


@pytest.mark.skipif(State is None, reason="sd_protocol not installed")
@pytest.mark.parametrize(
    "trace", ["msp_trace_synthetic.txt", "msp_trace_bench_udp.txt"]
)
def test_the_state_path_gives_the_same_topics_as_the_msp_path(trace):
    """Decision D2: /fc/attitude and /fc/battery made from STATE (the
    flight_interface ESP backend) equal the ones fc_telemetry makes from the
    same recorded FC replies, sample after sample (one yaw zero each)."""
    answers = load_trace(trace)
    pairs = list(zip(answers[msp.MSP_ATTITUDE], answers[msp.MSP_ANALOG]))
    assert len(pairs) >= 2
    msp_path, state_path = AttitudeConverter(), AttitudeConverter()
    for attitude_payload, analog_payload in pairs:
        fc = state_from_fc(attitude_payload, analog_payload)
        expected = msp_path.convert(msp.decode(msp.MSP_ATTITUDE, attitude_payload))
        assert state_path.convert(state_attitude(fc)) == pytest.approx(
            expected, abs=1e-12
        )
        want = battery_fields(msp.decode(msp.MSP_ANALOG, analog_payload), cell_count=4)
        got = battery_fields(state_analog(fc), cell_count=4)
        assert got["present"] == want["present"]
        assert got["voltage"] == pytest.approx(want["voltage"], nan_ok=True)
        assert len(got["cell_voltage"]) == len(want["cell_voltage"]) == 4


@pytest.mark.skipif(State is None, reason="sd_protocol not installed")
def test_the_state_path_on_the_real_bench_capture():
    """The 2026-11-06 capture through STATE: level, then ~38 deg clockwise
    with the right side and the nose down (the values the MSP test checks)."""
    answers = load_trace("msp_trace_bench_udp.txt")
    conv = AttitudeConverter()
    level, tilted = (
        state_from_fc(a, b)
        for a, b in zip(answers[msp.MSP_ATTITUDE], answers[msp.MSP_ANALOG])
    )
    assert conv.convert(state_attitude(level)) == pytest.approx(
        (-0.6 * DEG, 0.4 * DEG, 0.0)
    )
    assert conv.convert(state_attitude(tilted)) == pytest.approx(
        (18.7 * DEG, 21.7 * DEG, -38 * DEG)
    )
    assert battery_fields(state_analog(level), 4)["voltage"] == pytest.approx(15.53)
    assert battery_fields(state_analog(tilted), 4)["voltage"] == pytest.approx(15.52)
