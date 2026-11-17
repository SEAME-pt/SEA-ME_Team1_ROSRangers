"""MSP replies -> /fc/attitude and /fc/battery fields. Pure functions, no ROS.

Two sources, one conversion (decision D2): fc_telemetry decodes the FC's MSP
replies itself; the flight_interface ESP backend gets the same values inside
STATE, which the gateway copies from its own MSP polls (state_attitude() and
state_analog() turn them back into the decode() dicts below).

The protocol choices (docs/PraticalPlan/03 Architecture/Architecture Document.md,
"/fc/attitude and /fc/battery", pending team confirmation):
- angles in radians, REP-103 signs: roll + right side down, pitch + nose down,
  yaw + counter-clockwise seen from above;
- yaw relative to the first sample after start, wrapped to (-pi, pi];
- battery: only the voltage is measured, every other field is NaN.
"""

import math

# Betaflight's signs, turned into REP-103 by multiplying.
# Yaw was measured on 2026-11-02: clockwise reads positive.
# Roll and pitch were measured on 2026-11-06 (bench check M1, props off,
# Betaflight 2026.6.2, test/data/msp_trace_bench_udp.txt): right side down
# reads positive and nose up negative, so both already match REP-103
# (roll + right side down, pitch + nose down).
BF_ROLL_SIGN = 1.0
BF_PITCH_SIGN = 1.0
BF_YAW_SIGN = -1.0

POWER_SUPPLY_TECHNOLOGY_LIPO = 3  # sensor_msgs/BatteryState constant
POWER_SUPPLY_UNKNOWN = 0  # status and health: unknown
# Below this the FC runs on USB power only: Betaflight reports ~0 V with no
# battery, and even an empty 4S pack reads above 12 V.
NO_BATTERY_BELOW_V = 1.0


def wrap_pi(angle):
    """Wrap an angle in radians to (-pi, pi]."""
    wrapped = math.remainder(angle, 2.0 * math.pi)  # [-pi, pi]
    return math.pi if wrapped == -math.pi else wrapped


def midpoint_ns(t0_ns, t1_ns):
    """Stamp of an MSP sample: halfway between request sent and reply received."""
    return t0_ns + (t1_ns - t0_ns) // 2


class AttitudeConverter:
    """MSP_ATTITUDE decode() dict -> (roll, pitch, yaw) in rad, REP-103.

    Yaw is zero at the first sample: the drone has no magnetometer, so
    Betaflight's yaw is only the gyro integrated since FC power-up.
    """

    def __init__(self):
        self.yaw_zero_deg = None

    def reset_yaw(self):
        self.yaw_zero_deg = None

    def convert(self, attitude):
        if self.yaw_zero_deg is None:
            self.yaw_zero_deg = attitude["yaw_deg"]
        roll = BF_ROLL_SIGN * math.radians(attitude["roll_deg"])
        pitch = BF_PITCH_SIGN * math.radians(attitude["pitch_deg"])
        yaw = wrap_pi(
            BF_YAW_SIGN * math.radians(attitude["yaw_deg"] - self.yaw_zero_deg)
        )
        return roll, pitch, yaw


def battery_fields(analog, cell_count):
    """MSP_ANALOG decode() dict -> sensor_msgs/BatteryState fields.

    Uses the 0.01 V field when the FC sends it, else the legacy 0.1 V byte.
    With no battery connected: present is False and the voltage NaN, never a
    0 V reading that looks like a real one.
    """
    nan = float("nan")
    voltage = analog["voltage_v"] if "voltage_v" in analog else analog["vbat_v"]
    present = voltage >= NO_BATTERY_BELOW_V
    return {
        "voltage": voltage if present else nan,
        "temperature": nan,
        "current": nan,
        "charge": nan,
        "capacity": nan,
        "design_capacity": nan,
        "percentage": nan,
        "power_supply_status": POWER_SUPPLY_UNKNOWN,
        "power_supply_health": POWER_SUPPLY_UNKNOWN,
        "power_supply_technology": POWER_SUPPLY_TECHNOLOGY_LIPO,
        "present": present,
        # Cell count known, cell voltages not: one NaN per cell (BatteryState.msg)
        "cell_voltage": [nan] * cell_count,
        "cell_temperature": [nan] * cell_count,
    }


def state_attitude(fc_values):
    """STATE roll / pitch (0.1 deg) and heading (deg) -> the dict
    msp.decode() gives for MSP_ATTITUDE. The gateway copies them unchanged
    from MSP_ATTITUDE (sd::msp::decode_attitude), so the units are the same."""
    return {
        "roll_deg": fc_values["roll"] / 10,
        "pitch_deg": fc_values["pitch"] / 10,
        "yaw_deg": fc_values["heading"],
    }


def state_analog(fc_values):
    """STATE vbat (0.01 V) -> the dict msp.decode() gives for MSP_ANALOG. The
    gateway copies MSP_ANALOG's 0.01 V voltage field (sd::msp::decode_analog)."""
    return {"voltage_v": fc_values["vbat"] / 100}
