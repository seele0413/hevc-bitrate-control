import unittest

from hevc_lab.core.speed import classify_speed, speed_gate_passes


class SpeedPolicyTests(unittest.TestCase):
    def test_balanced_gate_rejects_below_point_nine_seven(self):
        self.assertTrue(speed_gate_passes(0.97, 0.97))
        self.assertFalse(speed_gate_passes(0.969, 0.97))
        self.assertEqual(classify_speed(0.97, 0.97), "near_realtime")

    def test_aggressive_without_gate_accepts_offline_speed(self):
        self.assertTrue(speed_gate_passes(0.2, None))
        self.assertEqual(classify_speed(0.2, None), "offline")

    def test_realtime_and_headroom_tiers(self):
        self.assertEqual(classify_speed(1.0, 0.97), "realtime")
        self.assertEqual(classify_speed(1.1, None), "realtime_headroom")

