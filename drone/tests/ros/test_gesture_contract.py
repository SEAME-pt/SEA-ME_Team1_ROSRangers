"""Contract between gesture_node and intent_node. No ROS needed.

intent_node only acts on the gesture names in GESTURE_TO_INTENT. Every one of
them must be a name that gesture_node can actually produce (recognize() on each
raised hand, then combine_hands()), otherwise renaming a gesture on one side
makes the drone silently ignore it.

Run with:  python3 -m pytest drone/tests/ros -v        (from the repo root)
or:        python3 -m unittest discover -s drone/tests/ros -v
"""

import unittest

from support import gesture_name, hands_for

from intent_node.gesture_filter import GESTURE_TO_INTENT, GestureFilter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ContractTests(unittest.TestCase):
    def test_every_mapped_gesture_can_be_recognised(self):
        for gesture in GESTURE_TO_INTENT:
            with self.subTest(gesture=gesture):
                self.assertEqual(gesture_name(hands_for(gesture)), gesture)

    def test_landmarks_to_intent(self):
        """gesture_node's name fed into the filter gives the mapped intent."""
        for gesture, intent in GESTURE_TO_INTENT.items():
            with self.subTest(gesture=gesture):
                f = GestureFilter(clock=FakeClock())
                name = gesture_name(hands_for(gesture))
                for _ in range(f.stable_packets):
                    f.update({"gesture": name, "t": 1.0, "xy": None})
                self.assertEqual(f.decide()["intent"], intent)


if __name__ == "__main__":
    unittest.main()
