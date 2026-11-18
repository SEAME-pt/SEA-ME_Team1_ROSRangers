"""Process tests for fc_telemetry: run it as a program, check how it ends.

Runs `python3 -m flight_interface.fc_telemetry` (the same main() that
`ros2 run` starts) as a subprocess, with transport:=udp against a fake FC on
127.0.0.1 (the same path as the ESP bridge), and checks:
  * it polls the FC and logs the first attitude;
  * Ctrl+C and SIGTERM are a normal stop: exit 0, no traceback;
  * bad configuration (unknown transport, no serial device, UDP port in use):
    exit 1 with a FATAL log line, no traceback.

Needs ROS 2 Jazzy sourced. Without rclpy the tests are skipped, unless
REQUIRE_ROS_TESTS=1 is set (CI sets it), then the import error fails the run.

Run with:  colcon test --packages-select flight_interface --event-handlers console_direct+
or:        python3 -m pytest test/test_fc_telemetry_process.py   (package folder, ROS sourced)
"""

import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest

from flight_interface import msp

try:
    import rclpy  # noqa: F401  (only to know whether ROS 2 is sourced)
except ImportError:
    if os.environ.get("REQUIRE_ROS_TESTS"):
        raise
    rclpy = None

# Own ROS domain (one per test file, `grep -r TEST_DOMAIN_ID` lists them):
# colcon tests packages in parallel, and running containers or teammates
# must not see these topics.
TEST_DOMAIN_ID = 90
NODE = [sys.executable, "-m", "flight_interface.fc_telemetry", "--ros-args"]
ANSWERS = {
    msp.MSP_ATTITUDE: struct.pack("<hhh", 12, -34, 56),
    msp.MSP_ANALOG: struct.pack("<BHHhH", 159, 0, 0, 0, 1590),
}


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class UdpFakeFc:
    """Answers MSP requests over UDP on 127.0.0.1, like the ESP bridge."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        parser = msp.Parser()
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            for frame in parser.feed(data):
                if frame.cmd in ANSWERS:
                    reply = msp.encode_response(frame.cmd, ANSWERS[frame.cmd])
                    self.sock.sendto(reply, addr)

    def close(self):
        self.running = False
        self.thread.join(2.0)
        self.sock.close()


class NodeProcess:
    """A node started as a subprocess, its output collected line by line."""

    def __init__(self, cmd):
        env = dict(os.environ, ROS_DOMAIN_ID=str(TEST_DOMAIN_ID), PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
        )
        self.lines = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line)

    @property
    def output(self):
        return "".join(self.lines)

    def wait_for_output(self, text, timeout=20.0):
        end = time.monotonic() + timeout
        while text not in self.output and self.proc.poll() is None:
            if time.monotonic() > end:
                break
            time.sleep(0.05)
        return text in self.output

    def finish(self, timeout=10.0):
        """Exit code, or None (and killed) if it still runs after `timeout` s."""
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            code = None
        self.reader.join(2.0)
        self.proc.stdout.close()
        return code

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.reader.join(2.0)
        self.proc.stdout.close()


@unittest.skipIf(rclpy is None, "ROS 2 (rclpy) not available")
class FcTelemetryProcessTests(unittest.TestCase):
    def start(self, *params):
        args = [arg for param in params for arg in ("-p", param)]
        node = NodeProcess(NODE + args)
        self.addCleanup(node.kill)
        return node

    def start_udp(self):
        fc = UdpFakeFc()
        self.addCleanup(fc.close)
        return self.start(
            "transport:=udp",
            "host:=127.0.0.1",
            f"port:={fc.port}",
            f"local_port:={free_udp_port()}",
        )

    def assertCleanStop(self, *signals):
        node = self.start_udp()
        self.assertTrue(node.wait_for_output("First attitude from the FC"), node.output)
        for sig in signals:
            node.proc.send_signal(sig)
            time.sleep(0.05)
        self.assertEqual(node.finish(), 0, node.output)
        self.assertNotIn("Traceback", node.output)

    def test_ctrl_c_stops_cleanly(self):
        self.assertCleanStop(signal.SIGINT)

    def test_double_ctrl_c_stops_cleanly(self):
        self.assertCleanStop(signal.SIGINT, signal.SIGINT)

    def test_sigterm_stops_cleanly(self):
        self.assertCleanStop(signal.SIGTERM)

    def test_unknown_transport_exits_1(self):
        node = self.start("transport:=bogus")
        self.assertEqual(node.finish(), 1, node.output)
        self.assertIn('transport must be "usb" or "udp"', node.output)
        self.assertNotIn("Traceback", node.output)

    def test_missing_serial_device_exits_1(self):
        node = self.start("transport:=usb", "device:=/nonexistent/ttyFC")
        self.assertEqual(node.finish(), 1, node.output)
        self.assertIn("Cannot open /nonexistent/ttyFC", node.output)
        self.assertNotIn("Traceback", node.output)

    def test_udp_port_in_use_exits_1(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
            busy.bind(("0.0.0.0", 0))
            port = busy.getsockname()[1]
            node = self.start("transport:=udp", f"local_port:={port}")
            self.assertEqual(node.finish(), 1, node.output)
        self.assertIn(f"Cannot listen on UDP {port}", node.output)
        self.assertNotIn("Traceback", node.output)
