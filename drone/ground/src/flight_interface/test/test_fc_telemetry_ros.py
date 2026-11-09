"""ROS 2 tests for fc_telemetry: real node, real topics, a fake FC.

The node gets an msp.Client over FakeFc (test/fake_fc.py) instead of the USB
or UDP transport, and a probe node records /fc/attitude and /fc/battery.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select flight_interface --event-handlers console_direct+
or:        python3 -m pytest test/test_fc_telemetry_ros.py   (package folder, ROS sourced)
"""

import math
import os
import struct
import time
import unittest

from fake_fc import FakeFc, load_trace

from flight_interface import msp

try:
    import rclpy
    from geometry_msgs.msg import Vector3Stamped
    from rclpy.executors import SingleThreadedExecutor
    from sensor_msgs.msg import BatteryState

    from flight_interface.fc_telemetry import FcTelemetry
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 89
DEG = math.pi / 180


def attitude_payload(roll_deg, pitch_deg, yaw_deg):
    return struct.pack("<hhh", round(roll_deg * 10), round(pitch_deg * 10), yaw_deg)


def analog_payload(volts):
    return struct.pack("<BHHhH", round(volts * 10), 0, 0, 0, round(volts * 100))


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class FcTelemetryRosTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fast timers so the end-to-end test needs well under a second
        args = ["--ros-args", "-p", "attitude_hz:=50.0", "-p", "battery_hz:=20.0"]
        rclpy.init(args=args, domain_id=TEST_DOMAIN_ID)

    @classmethod
    def tearDownClass(cls):
        rclpy.shutdown()

    def start(self, answers):
        self.fc = FakeFc(answers)
        self.node = FcTelemetry(client=msp.Client(self.fc))
        self.probe = rclpy.create_node("fc_telemetry_test_probe")
        self.attitudes, self.batteries = [], []
        self.probe.create_subscription(
            Vector3Stamped, "/fc/attitude", self.attitudes.append, 10
        )
        self.probe.create_subscription(
            BatteryState, "/fc/battery", self.batteries.append, 10
        )
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.executor.add_node(self.probe)
        self.stopped = False
        self.addCleanup(self.stop)

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.executor.shutdown()
        self.node.destroy_node()
        self.probe.destroy_node()

    def spin_until(self, condition, timeout=3.0):
        end = time.monotonic() + timeout
        while not condition() and time.monotonic() < end:
            self.executor.spin_once(timeout_sec=0.02)
        return condition()

    def deliver(self):
        """Let the probe receive what the node published, without new polls."""
        end = time.monotonic() + 0.3
        while time.monotonic() < end:
            rclpy.spin_once(self.probe, timeout_sec=0.02)

    def test_publishes_attitude_and_battery_from_the_trace(self):
        self.start(load_trace("msp_trace_synthetic.txt"))
        self.assertTrue(self.spin_until(lambda: len(self.attitudes) >= 2))
        self.assertTrue(self.spin_until(lambda: len(self.batteries) >= 2))

        first, second = self.attitudes[:2]
        self.assertEqual(first.header.frame_id, "base_link")
        self.assertAlmostEqual(first.vector.x, -0.6 * DEG)
        self.assertAlmostEqual(first.vector.y, -0.3 * DEG)
        self.assertEqual(first.vector.z, 0.0)
        self.assertAlmostEqual(second.vector.x, 171.3 * DEG)
        self.assertAlmostEqual(second.vector.z, -83 * DEG)

        battery = self.batteries[0]
        self.assertEqual(battery.header.frame_id, "base_link")
        self.assertAlmostEqual(battery.voltage, 15.9, places=4)
        self.assertTrue(math.isnan(battery.percentage))
        self.assertEqual(len(battery.cell_voltage), 4)
        self.assertTrue(battery.present)

    def test_publishes_from_the_real_bridge_trace(self):
        # 2026-11-06 capture through the ESP bridge: level, then tilted
        self.start(load_trace("msp_trace_bench_udp.txt"))
        self.assertTrue(self.spin_until(lambda: len(self.attitudes) >= 2))
        self.assertTrue(self.spin_until(lambda: len(self.batteries) >= 2))

        level, tilted = self.attitudes[:2]
        self.assertAlmostEqual(level.vector.x, -0.6 * DEG)
        self.assertAlmostEqual(level.vector.y, 0.4 * DEG)
        self.assertEqual(level.vector.z, 0.0)
        self.assertAlmostEqual(tilted.vector.x, 18.7 * DEG)  # right side down
        self.assertAlmostEqual(tilted.vector.y, 21.7 * DEG)  # nose down
        self.assertAlmostEqual(tilted.vector.z, -38 * DEG)  # clockwise
        self.assertAlmostEqual(self.batteries[0].voltage, 15.53, places=4)
        self.assertAlmostEqual(self.batteries[1].voltage, 15.52, places=4)

    def test_stamp_lies_between_request_and_reply(self):
        self.start({msp.MSP_ATTITUDE: [attitude_payload(1.0, 2.0, 3)]})
        before = self.node.get_clock().now().nanoseconds
        self.node._attitude_tick()
        after = self.node.get_clock().now().nanoseconds
        self.deliver()
        self.assertEqual(len(self.attitudes), 1)
        stamp = self.attitudes[0].header.stamp
        stamp_ns = stamp.sec * 1_000_000_000 + stamp.nanosec
        self.assertTrue(before <= stamp_ns <= after)

    def test_no_reply_publishes_nothing(self):
        self.start({})
        self.node._attitude_tick()
        self.node._battery_tick()
        self.deliver()
        self.assertEqual(self.attitudes, [])
        self.assertEqual(self.batteries, [])
        self.assertEqual(self.node._missed, {"attitude": 1, "battery": 1})
        self.assertEqual(self.fc.requests, [msp.MSP_ATTITUDE, msp.MSP_ANALOG])

    def test_error_reply_publishes_nothing(self):
        self.start({msp.MSP_ATTITUDE: ["error"]})
        self.node._attitude_tick()
        self.deliver()
        self.assertEqual(self.attitudes, [])
        self.assertEqual(self.node._missed["attitude"], 1)

    def test_short_payload_publishes_nothing(self):
        self.start({msp.MSP_ATTITUDE: [b"\x01\x02"]})
        self.node._attitude_tick()
        self.deliver()
        self.assertEqual(self.attitudes, [])

    def test_late_reply_from_an_earlier_request_is_dropped(self):
        self.start({msp.MSP_ATTITUDE: [attitude_payload(0.0, 0.0, 100)]})
        # A reply that arrived after its request timed out, still in the buffer
        self.fc.inbox += msp.encode_response(
            msp.MSP_ATTITUDE, attitude_payload(45.0, 45.0, 0)
        )
        self.node._attitude_tick()
        self.deliver()
        self.assertEqual(len(self.attitudes), 1)
        self.assertEqual(self.attitudes[0].vector.x, 0.0)  # the fresh reply

    def test_transport_error_publishes_nothing(self):
        self.start({})

        def broken_send(data):
            raise OSError("USB unplugged")

        self.fc.send = broken_send
        self.node._attitude_tick()
        self.deliver()
        self.assertEqual(self.attitudes, [])
        self.assertEqual(self.node._missed["attitude"], 1)

    def test_unplugged_usb_recv_error_publishes_nothing(self):
        # pyserial's read() raises SerialException (an OSError) once the FC
        # is unplugged; the drain before each request hits it first
        self.start({})

        def broken_recv():
            raise OSError("device disconnected")

        self.fc.recv = broken_recv
        self.node._attitude_tick()
        self.node._battery_tick()
        self.deliver()
        self.assertEqual(self.attitudes, [])
        self.assertEqual(self.batteries, [])
        self.assertEqual(self.node._missed["attitude"], 1)
        self.assertEqual(self.node._missed["battery"], 1)

    def test_no_battery_publishes_not_present(self):
        self.start({msp.MSP_ANALOG: [analog_payload(0.0)]})
        self.node._battery_tick()
        self.deliver()
        self.assertEqual(len(self.batteries), 1)
        self.assertFalse(self.batteries[0].present)
        self.assertTrue(math.isnan(self.batteries[0].voltage))

    def test_link_report_with_and_without_replies(self):
        self.start({msp.MSP_ANALOG: [analog_payload(16.0)]})
        self.node._report_link()  # nothing yet: warning
        self.node._battery_tick()
        self.node._report_link()  # one sample: round trip logged
        self.assertEqual(self.node._rtts_ms, [])
        self.assertEqual(self.node._window, {"attitude": 0, "battery": 0})

    def test_destroy_closes_the_transport(self):
        self.start({})
        self.stop()
        self.assertTrue(self.fc.closed)


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class FcTelemetryConfigTests(unittest.TestCase):
    """Bad configuration raises; main() turns that into exit code 1."""

    def node_with(self, *params):
        args = ["--ros-args"] + [arg for p in params for arg in ("-p", p)]
        rclpy.init(args=args, domain_id=TEST_DOMAIN_ID)
        self.addCleanup(rclpy.shutdown)
        return FcTelemetry()

    def test_unknown_transport_raises(self):
        with self.assertRaises(ValueError):
            self.node_with("transport:=bogus")

    def test_missing_serial_device_raises(self):
        with self.assertRaises(OSError):
            self.node_with("transport:=usb", "device:=/nonexistent/ttyFC")
