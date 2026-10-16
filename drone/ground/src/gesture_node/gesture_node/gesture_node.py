#!/usr/bin/env python3
"""gesture_node: gestures.py (UDP, JSON) -> filter -> /gesture/intent.

Single-threaded: one timer drains the UDP socket, another publishes.
No threads, no locks.
"""
import json
import socket
import sys

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from gesture_node.gesture_filter import GestureFilter

POLL_PERIOD_SEC = 0.01        # drain the socket 100 times per second
MAX_DATAGRAMS_PER_POLL = 200
LINK_REPORT_SEC = 5.0


class GestureNode(Node):
    def __init__(self):
        super().__init__("gesture_node")

        self.declare_parameter("udp_host", "127.0.0.1")
        self.declare_parameter("udp_port", 5005)
        self.declare_parameter("intent_topic", "/gesture/intent")
        self.declare_parameter("stable_packets", 5)
        self.declare_parameter("neutral_timeout_sec", 0.3)
        self.declare_parameter("stale_timeout_sec", 2.0)
        self.declare_parameter("publish_rate_hz", 20.0)

        host = str(self.get_parameter("udp_host").value)
        port = int(self.get_parameter("udp_port").value)
        topic = str(self.get_parameter("intent_topic").value)
        rate = max(1.0, float(self.get_parameter("publish_rate_hz").value))

        self._filter = GestureFilter(
            stable_packets=self.get_parameter("stable_packets").value,
            neutral_timeout=self.get_parameter("neutral_timeout_sec").value,
            stale_timeout=self.get_parameter("stale_timeout_sec").value,
        )

        self._sock = self._open_socket(host, port)
        self._pub = self.create_publisher(String, topic, 10)

        self._packets_total = 0
        self._packets_window = 0
        self._bad_total = 0
        self._last_gesture_seen = None
        self._last_signature = None

        self.create_timer(POLL_PERIOD_SEC, self._poll_udp)
        self.create_timer(1.0 / rate, self._publish_tick)
        self.create_timer(LINK_REPORT_SEC, self._report_link)

        self.get_logger().info(
            f"Listening for gestures.py on UDP {host}:{port}; "
            f"publishing JSON strings on {topic} at {rate:.0f} Hz")

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
                "Is another gesture_node running, or is the port in use?")
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
                    f"UDP receive error: {exc}", throttle_duration_sec=5.0)
                return

            try:
                packet = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                packet = None
            if not isinstance(packet, dict):
                self._bad_total += 1
                self.get_logger().warning(
                    "Ignored a datagram that is not a JSON object",
                    throttle_duration_sec=5.0)
                continue

            if self._packets_total == 0:
                self.get_logger().info(f"First packet from gestures.py: {packet}")
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
                f"(reason: {result['reason']}, gesture: {result['gesture']})")

    def _report_link(self):
        count = self._packets_window
        self._packets_window = 0
        if count == 0:
            self.get_logger().warning(
                "No packets from gestures.py in the last "
                f"{LINK_REPORT_SEC:.0f} s (total received: {self._packets_total})")
        else:
            self.get_logger().info(
                f"Recogniser link OK: {count} packets in the last "
                f"{LINK_REPORT_SEC:.0f} s, last gesture seen: "
                f"{self._last_gesture_seen}")

    def destroy_node(self):
        try:
            self._sock.close()
        except OSError:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    exit_code = 0
    try:
        node = GestureNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except OSError:
        exit_code = 1                     # already logged by _open_socket
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
