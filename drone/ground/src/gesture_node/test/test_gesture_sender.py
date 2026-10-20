"""Unit tests for the topic gesture_sender. No ROS, MediaPipe or camera needed.

Run with:  python3 -m unittest discover -s test -v      (from the package folder)
or:        colcon test --packages-select gesture_node   (uses pytest)
"""

import json
import unittest
from unittest import mock

from gesture_node import gesture_sender


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.clock = FakeClock()
        patcher = mock.patch.object(gesture_sender.time, "monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        gesture_sender.set_publisher(self.sent.append)
        self.addCleanup(gesture_sender.set_publisher, None)

    def packets(self):
        return [json.loads(text) for text in self.sent]

    def test_packet_has_the_udp_format(self):
        gesture_sender.send_gesture("INDEX_RIGHT", [0.25, 0.75])
        (packet,) = self.packets()
        self.assertEqual(set(packet), {"gesture", "t", "xy"})
        self.assertEqual(packet["gesture"], "INDEX_RIGHT")
        self.assertEqual(packet["xy"], [0.25, 0.75])
        self.assertIsInstance(packet["t"], float)

    def test_no_hand_is_sent_as_null(self):
        gesture_sender.send_gesture(None)
        self.assertIsNone(self.packets()[0]["gesture"])

    def test_change_is_sent_at_once(self):
        gesture_sender.send_gesture("CLOSED_HAND")
        gesture_sender.send_gesture("OPEN_HAND_UP")
        self.assertEqual(
            [p["gesture"] for p in self.packets()], ["CLOSED_HAND", "OPEN_HAND_UP"]
        )

    def test_same_gesture_waits_for_keepalive(self):
        gesture_sender.send_gesture("CLOSED_HAND")
        self.clock.advance(gesture_sender.KEEPALIVE_SEC / 2)
        gesture_sender.send_gesture("CLOSED_HAND")
        self.assertEqual(len(self.sent), 1)
        self.clock.advance(gesture_sender.KEEPALIVE_SEC)
        gesture_sender.send_gesture("CLOSED_HAND")
        self.assertEqual(len(self.sent), 2)

    def test_nothing_sent_without_publisher(self):
        gesture_sender.set_publisher(None)
        gesture_sender.send_gesture("CLOSED_HAND")
        self.assertEqual(self.sent, [])

    def test_first_packet_after_new_publisher_goes_out(self):
        gesture_sender.send_gesture("CLOSED_HAND")
        gesture_sender.set_publisher(self.sent.append)
        gesture_sender.send_gesture("CLOSED_HAND")
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
