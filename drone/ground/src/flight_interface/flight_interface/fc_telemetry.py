#!/usr/bin/env python3
"""fc_telemetry: polls the FC over MSP and publishes /fc/attitude and /fc/battery.

transport:=usb talks to the FC's own USB port, transport:=udp to the ESP's
transparent bridge. Both go through msp.Client, read-only.

Single-threaded: two timers poll the FC, one at a time, so a request and its
reply never interleave with another. Each sample is stamped halfway between
request and reply (ROS clock). A missing or bad reply publishes nothing.
Late replies are dropped if they arrived before the next request (MSP v1 has
no sequence number, so one that arrives later can still be taken).

A transport error while running (USB unplugged, network down) is a warning
and nothing is published; the node keeps running but does not reopen the
port, so restart it after plugging the FC back in.

Ctrl+C and SIGTERM are a normal stop: exit code 0. A transport that cannot be
opened (no FC on the serial port, UDP port in use) or an unknown transport:
FATAL log and exit code 1.
"""

import signal
import statistics
import sys
import threading

import rclpy
from geometry_msgs.msg import Vector3Stamped
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from rclpy.time import Time
from sensor_msgs.msg import BatteryState

from flight_interface import msp
from flight_interface.telemetry import (
    AttitudeConverter,
    battery_fields,
    midpoint_ns,
)

ATTITUDE_TOPIC = "/fc/attitude"
BATTERY_TOPIC = "/fc/battery"
LINK_REPORT_SEC = 5.0
MAX_DRAIN_READS = 20  # leftover bytes are at most a few late replies


class FcTelemetry(Node):
    def __init__(self, client=None):
        """`client` replaces the transport from the parameters (tests)."""
        super().__init__("fc_telemetry")

        self.declare_parameter("transport", "usb")  # "usb" or "udp"
        self.declare_parameter("device", "/dev/ttyACM0")
        self.declare_parameter("baud", 115200)
        self.declare_parameter("host", "192.168.4.1")  # ESP bridge
        self.declare_parameter("port", 5760)  # ESP bridge UDP port
        self.declare_parameter("local_port", 5760)  # PC UDP port the ESP sends to
        self.declare_parameter("attitude_hz", 20.0)
        self.declare_parameter("battery_hz", 5.0)
        self.declare_parameter("request_timeout_sec", 0.1)
        self.declare_parameter("frame_id", "base_link")
        self.declare_parameter("cell_count", 4)  # CNHL 4S pack

        self._timeout = float(self.get_parameter("request_timeout_sec").value)
        self._frame_id = str(self.get_parameter("frame_id").value)
        self._cell_count = int(self.get_parameter("cell_count").value)
        attitude_hz = max(0.1, float(self.get_parameter("attitude_hz").value))
        battery_hz = max(0.1, float(self.get_parameter("battery_hz").value))

        if client is None:
            client, where = self._open_client()
        else:
            where = "injected client"
        self._client = client

        self._attitude = AttitudeConverter()
        self._attitude_pub = self.create_publisher(Vector3Stamped, ATTITUDE_TOPIC, 10)
        self._battery_pub = self.create_publisher(BatteryState, BATTERY_TOPIC, 10)

        self._published = {"attitude": 0, "battery": 0}
        self._missed = {"attitude": 0, "battery": 0}
        self._window = {"attitude": 0, "battery": 0}
        self._rtts_ms = []
        self._first_logged = False

        self.create_timer(1.0 / attitude_hz, self._attitude_tick)
        self.create_timer(1.0 / battery_hz, self._battery_tick)
        self.create_timer(LINK_REPORT_SEC, self._report_link)

        self.get_logger().info(
            f"Polling the FC ({where}); publishing {ATTITUDE_TOPIC} at "
            f"{attitude_hz:g} Hz and {BATTERY_TOPIC} at {battery_hz:g} Hz, "
            f"frame_id {self._frame_id}"
        )

    def _open_client(self):
        transport = str(self.get_parameter("transport").value)
        if transport == "usb":
            device = str(self.get_parameter("device").value)
            baud = int(self.get_parameter("baud").value)
            try:
                t = msp.SerialTransport(device, baud)
            except OSError as exc:  # serial.SerialException is an OSError
                self.get_logger().fatal(
                    f"Cannot open {device}: {exc}. Is the FC plugged in, and "
                    "is no other program (Configurator, msp_check) using it?"
                )
                raise
            return msp.Client(t), f"FC over USB on {device}"
        if transport == "udp":
            host = str(self.get_parameter("host").value)
            port = int(self.get_parameter("port").value)
            local_port = int(self.get_parameter("local_port").value)
            try:
                t = msp.UdpTransport(host, port, local_port)
            except OSError as exc:
                self.get_logger().fatal(
                    f"Cannot listen on UDP {local_port}: {exc}. "
                    "Is msp_check or another fc_telemetry running?"
                )
                raise
            return msp.Client(t), f"ESP bridge at {host}:{port}, local UDP {local_port}"
        self.get_logger().fatal(f'transport must be "usb" or "udp", got "{transport}"')
        raise ValueError(transport)

    def _drain(self):
        """Drop bytes left from an earlier request.

        msp.Client takes the first reply with the right command, so a reply
        that came after its timeout would otherwise answer the next request:
        old data with a new stamp. This only drops replies that arrived before
        the next request: MSP v1 has no sequence number, so a timed-out reply
        that arrives after the drain is still taken as the next answer.
        """
        for _ in range(MAX_DRAIN_READS):
            data = self._client.t.recv()
            if not data:
                return
            self._client.parser.feed(data)

    def _poll(self, name, cmd):
        """One MSP request. Returns (decoded dict, stamp in ns) or None."""
        try:
            self._drain()  # recv() raises too when the USB is unplugged
            t0 = self.get_clock().now().nanoseconds
            frame, _ = self._client.request(cmd, timeout=self._timeout)
        except OSError as exc:  # USB unplugged, network down
            frame = None
            self.get_logger().warning(
                f"{name}: transport error: {exc}", throttle_duration_sec=5.0
            )
        t1 = self.get_clock().now().nanoseconds
        decoded = msp.decode(cmd, frame.payload) if frame is not None else None
        if frame is None or frame.direction != ">" or "raw" in decoded:
            self._missed[name] += 1
            self.get_logger().warning(
                f"{name}: no valid reply within {self._timeout * 1000:.0f} ms; "
                "nothing published",
                throttle_duration_sec=5.0,
            )
            return None
        self._rtts_ms.append((t1 - t0) / 1e6)
        self._window[name] += 1
        return decoded, midpoint_ns(t0, t1)

    def _attitude_tick(self):
        sample = self._poll("attitude", msp.MSP_ATTITUDE)
        if sample is None:
            return
        attitude, stamp_ns = sample
        if not self._first_logged:
            self._first_logged = True
            self.get_logger().info(f"First attitude from the FC: {attitude}")
        msg = Vector3Stamped()
        msg.header.stamp = Time(nanoseconds=stamp_ns).to_msg()
        msg.header.frame_id = self._frame_id
        msg.vector.x, msg.vector.y, msg.vector.z = self._attitude.convert(attitude)
        self._attitude_pub.publish(msg)
        self._published["attitude"] += 1

    def _battery_tick(self):
        sample = self._poll("battery", msp.MSP_ANALOG)
        if sample is None:
            return
        analog, stamp_ns = sample
        msg = BatteryState()
        msg.header.stamp = Time(nanoseconds=stamp_ns).to_msg()
        msg.header.frame_id = self._frame_id
        for field, value in battery_fields(analog, self._cell_count).items():
            setattr(msg, field, value)
        self._battery_pub.publish(msg)
        self._published["battery"] += 1

    def _report_link(self):
        rtts, self._rtts_ms = self._rtts_ms, []
        counts = dict(self._window)
        self._window = {"attitude": 0, "battery": 0}
        missed = (
            f"missed since start: attitude {self._missed['attitude']}, "
            f"battery {self._missed['battery']}"
        )
        if not rtts:
            self.get_logger().warning(
                f"No replies from the FC in the last {LINK_REPORT_SEC:.0f} s ({missed})"
            )
            return
        self.get_logger().info(
            f"FC link OK: {counts['attitude']} attitude, {counts['battery']} "
            f"battery in the last {LINK_REPORT_SEC:.0f} s; MSP round trip median "
            f"{statistics.median(rtts):.1f} ms, max {max(rtts):.1f} ms ({missed})"
        )

    def destroy_node(self):
        try:
            self._client.close()
        except OSError:
            pass
        super().destroy_node()


def main(args=None):
    # Ctrl+C and SIGTERM only ask the spin loop below to stop (same pattern as
    # intent_node: rclpy's own handler races the executor).
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stop.set())
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    try:
        node = FcTelemetry()
    except (OSError, ValueError):
        rclpy.shutdown()
        sys.exit(1)  # already logged by _open_client
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    try:
        while not stop.is_set():
            executor.spin_once(timeout_sec=0.1)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
