import unittest

from hevc_lab.experiment import calculate_saving, is_deployable, select_candidate
from hevc_lab.models import CandidateResult


def candidate(name, bitrate, eligible=True, speed=1.5):
    return CandidateResult(
        name=name,
        title=name,
        description="",
        output_path=f"{name}.mp4",
        x265_params="",
        crf=22,
        preset="medium",
        bitrate_bps=bitrate,
        file_size_bytes=1,
        vmaf_mean=95,
        vmaf_p5=90,
        ssim=0.99,
        encode_seconds=1,
        encode_speed_x=speed,
        quality_pass=eligible,
        speed_pass=eligible,
        eligible=eligible,
    )


class SelectionTests(unittest.TestCase):
    def test_selects_lowest_bitrate_eligible_candidate(self):
        selected = select_candidate(
            [candidate("a", 900), candidate("b", 700), candidate("c", 500, False)]
        )
        self.assertEqual(selected.name, "b")

    def test_returns_none_when_nothing_is_eligible(self):
        self.assertIsNone(select_candidate([candidate("a", 100, False)]))

    def test_saving_sign(self):
        self.assertAlmostEqual(calculate_saving(1000, 800), 20.0)
        self.assertAlmostEqual(calculate_saving(1000, 1200), -20.0)

    def test_deployment_rejects_negative_or_too_small_saving(self):
        item = candidate("a", 800)
        item.bitrate_saving_vs_baseline_pct = 10.0
        item.bitrate_saving_vs_source_pct = -10.0
        self.assertFalse(is_deployable(item, 5.0))
        item.bitrate_saving_vs_source_pct = 4.99
        self.assertFalse(is_deployable(item, 5.0))
        item.bitrate_saving_vs_source_pct = 5.0
        self.assertTrue(is_deployable(item, 5.0))

    def test_deployment_requires_algorithm_improvement_and_mode_threshold(self):
        item = candidate("optimized", 800)
        item.bitrate_saving_vs_source_pct = 20.0
        item.bitrate_saving_vs_baseline_pct = -1.0
        self.assertFalse(is_deployable(item, 5.0, 0.0))
        item.bitrate_saving_vs_baseline_pct = 4.99
        self.assertFalse(is_deployable(item, 5.0, 5.0))
        item.bitrate_saving_vs_baseline_pct = 5.0
        self.assertTrue(is_deployable(item, 5.0, 5.0))


if __name__ == "__main__":
    unittest.main()
