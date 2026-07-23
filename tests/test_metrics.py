import unittest

from hevc_lab.metrics import parse_ssim_output, percentile


class MetricTests(unittest.TestCase):
    def test_percentile_uses_linear_interpolation(self):
        self.assertAlmostEqual(percentile([0, 10, 20, 30, 40], 5), 2.0)

    def test_parse_ssim_uses_last_summary(self):
        text = "frame line\nSSIM Y:0.99 U:0.98 V:0.98 All:0.987654 (18.9)"
        self.assertAlmostEqual(parse_ssim_output(text), 0.987654)


if __name__ == "__main__":
    unittest.main()

