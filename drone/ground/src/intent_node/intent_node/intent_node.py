#!/usr/bin/env python3
"""intent_node: gestures.py (UDP, JSON) -> filter -> /gesture/intent.

input_source:=topic reads the same JSON from gesture_node on /gesture/raw
(std_msgs/String) instead of UDP. Both go through _handle_datagram().

Single-threaded: one timer drains the UDP socket (or the topic callback runs),
another publishes. No threads, no locks.

Ctrl+C and SIGTERM (docker stop) are a normal stop: exit code 0. An unknown
input_source or a busy UDP port: FATAL log and exit code 1.
"""

import json
import signal
import socket
import sys
import threading

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import String

from intent_node.gesture_filter import GestureFilter

POLL_PERIOD_SEC = 0.01  # drain the socket 100 times per second
MAX_DATAGRAMS_PER_POLL = 200
LINK_REPORT_SEC = 5.0


class IntentNode(Node):
    def __init__(self):
        super().__init__("intent_node")

        self.declare_parameter("input_source", "udp")  # "udp" or "topic"
        self.declare_parameter("udp_host", "127.0.0.1")
        self.declare_parameter("udp_port", 5005)
        self.declare_parameter("raw_topic", "/gesture/raw")
        self.declare_parameter("intent_topic", "/gesture/intent")
        self.declare_parameter("stable_packets", 5)
        self.declare_parameter("neutral_timeout_sec", 0.3)
        self.declare_parameter("stale_timeout_sec", 2.0)
        self.declare_parameter("publish_rate_hz", 20.0)

        source = str(self.get_parameter("input_source").value)
        host = str(self.get_parameter("udp_host").value)
        port = int(self.get_parameter("udp_port").value)
        raw_topic = str(self.get_parameter("raw_topic").value)
        topic = str(self.get_parameter("intent_topic").value)
        if source not in ("udp", "topic"):
            self.get_logger().fatal(
                f'input_source must be "udp" or "topic", got "{source}"'
            )
            raise ValueError(source)
        rate = max(1.0, float(self.get_parameter("publish_rate_hz").value))

        self._filter = GestureFilter(
            stable_packets=self.get_parameter("stable_packets").value,
            neutral_timeout=self.get_parameter("neutral_timeout_sec").value,
            stale_timeout=self.get_parameter("stale_timeout_sec").value,
        )

        self._sock = None
        if source == "udp":
            self._sock = self._open_socket(host, port)
            self._sender = "gestures.py"
            where = f"gestures.py on UDP {host}:{port}"
        else:
            self.create_subscription(String, raw_topic, self._on_raw, 10)
            self._sender = "gesture_node"
            where = f"gesture_node on {raw_topic}"
        self._pub = self.create_publisher(String, topic, 10)

        self._packets_total = 0
        self._packets_window = 0
        self._bad_total = 0
        self._last_gesture_seen = None
        self._last_signature = None

        if self._sock is not None:
            self.create_timer(POLL_PERIOD_SEC, self._poll_udp)
        self.create_timer(1.0 / rate, self._publish_tick)
        self.create_timer(LINK_REPORT_SEC, self._report_link)

        self.get_logger().info(
            f"Listening for {where}; "
            f"publishing JSON strings on {topic} at {rate:.0f} Hz"
        )

    def _open_socket(self, host, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No SO_REUSEADDR on purpose: a second node on the same port must fail
        # loudly instead of silently sharing packets.
        try:
            sock.bind((host, port))
        except OSError as exc:
            sock.close()
            self.get_logger().fatal(
                f"Cannot listen on UDP {host}:{port}: {exc}. "
                "Is another intent_node running, or is the port in use?"
            )
            raise
        sock.setblocking(False)
        return sock

    def _poll_udp(self):
        for _ in range(MAX_DATAGRAMS_PER_POLL):
            try:
                raw, _addr = self._sock.recvfrom(4096)
            except BlockingIOError:
                return
            except OSError as exc:
                self.get_logger().warning(
                    f"UDP receive error: {exc}", throttle_duration_sec=5.0
                )
                return
            self._handle_datagram(raw)

    def _on_raw(self, msg):
        self._handle_datagram(msg.data.encode("utf-8"))

    def _handle_datagram(self, raw):
        try:
            packet = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            packet = None
        if not isinstance(packet, dict):
            self._bad_total += 1
            self.get_logger().warning(
                "Ignored a datagram that is not a JSON object",
                throttle_duration_sec=5.0,
            )
            return

        if self._packets_total == 0:
            self.get_logger().info(f"First packet from {self._sender}: {packet}")
        self._packets_total += 1
        self._packets_window += 1
        self._last_gesture_seen = packet.get("gesture")
        self._filter.update(packet)

    def _publish_tick(self):
        result = self._filter.decide()
        msg = String()
        msg.data = json.dumps(result, separators=(",", ":"))
        self._pub.publish(msg)

        signature = (result["intent"], result["reason"])
        if signature != self._last_signature:
            self._last_signature = signature
            self.get_logger().info(
                f"Intent -> {result['intent']} "
                f"(reason: {result['reason']}, gesture: {result['gesture']})"
            )

    def _report_link(self):
        count = self._packets_window
        self._packets_window = 0
        ignored = ""
        if self._bad_total:
            ignored = f"; {self._bad_total} ignored since start (not JSON objects)"
        if count == 0:
            self.get_logger().warning(
                f"No packets from {self._sender} in the last "
                f"{LINK_REPORT_SEC:.0f} s (total received: {self._packets_total})"
                f"{ignored}"
            )
        else:
            self.get_logger().info(
                f"Recogniser link OK: {count} packets in the last "
                f"{LINK_REPORT_SEC:.0f} s, last gesture seen: "
                f"{self._last_gesture_seen}{ignored}"
            )

    def destroy_node(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        super().destroy_node()


def main(args=None):
    # Ctrl+C and SIGTERM (docker stop) only ask the spin loop below to stop.
    # rclpy's own handler would shut rclpy down from another thread while the
    # executor builds its next wait set, which then fails with an RCLError. A
    # repeated signal (ros2 launch forwards Ctrl+C) changes nothing.
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda signum, frame: stop.set())
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    try:
        node = IntentNode()
    except (OSError, ValueError):
        rclpy.shutdown()
        sys.exit(1)  # already logged by _open_socket / the input_source check
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    try:
        while not stop.is_set():
            executor.spin_once(timeout_sec=0.1)  # a signal is seen within 0.1 s
    finally:
        # Stopping now: ignore more Ctrl+C / SIGTERM (ros2 launch forwards one
        # more). A Python handler is removed while the interpreter exits, and a
        # signal arriving then would kill the process (exit code -2).
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
