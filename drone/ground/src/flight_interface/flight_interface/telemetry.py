"""MSP replies -> /fc/attitude and /fc/battery fields. Pure functions, no ROS.

The protocol choices (MINE/Decisions.md, pending team confirmation):
- angles in radians, REP-103 signs: roll + right side down, pitch + nose down,
  yaw + counter-clockwise seen from above;
- yaw relative to the first sample after start, wrapped to (-pi, pi];
- battery: only the voltage is measured, every other field is NaN.
"""

import math

# Betaflight's signs, turned into REP-103 by multiplying.
# Yaw was measured on 2026-11-02: clockwise reads positive.
# Roll and pitch are NOT measured yet (bench check M1, props off): assumed
# aerospace-like, roll + right side down and pitch + nose up. Fix them here.
BF_ROLL_SIGN = 1.0
BF_PITCH_SIGN = -1.0
BF_YAW_SIGN = -1.0

POWER_SUPPLY_TECHNOLOGY_LIPO = 3  # sensor_msgs/BatteryState constant
POWER_SUPPLY_UNKNOWN = 0  # status and health: unknown


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
    """
    nan = float("nan")
    return {
        "voltage": analog.get("voltage_v", analog["vbat_v"]),
        "temperature": nan,
        "current": nan,
        "charge": nan,
        "capacity": nan,
        "design_capacity": nan,
        "percentage": nan,
        "power_supply_status": POWER_SUPPLY_UNKNOWN,
        "power_supply_health": POWER_SUPPLY_UNKNOWN,
        "power_supply_technology": POWER_SUPPLY_TECHNOLOGY_LIPO,
        "present": True,
        # Cell count known, cell voltages not: one NaN per cell (BatteryState.msg)
        "cell_voltage": [nan] * cell_count,
        "cell_temperature": [nan] * cell_count,
    }
