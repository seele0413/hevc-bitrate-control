import unittest

from hevc_lab.core.models import CandidateResult
from hevc_lab.core.search import (
    QualitySearchSpec,
    QualityThresholds,
    adaptive_quality_search,
)


def point(crf, vmaf, p5, ssim=0.995):
    return CandidateResult(
        name="optimized",
        title="optimized",
        description="",
        output_path=f"crf-{crf:.1f}.mp4",
        x265_params="",
        crf=crf,
        preset="medium",
        bitrate_bps=1_000_000 - crf * 10_000,
        file_size_bytes=1,
        vmaf_mean=vmaf,
        vmaf_p5=p5,
        ssim=ssim,
        encode_seconds=1,
        encode_speed_x=2,
        quality_pass=False,
        speed_pass=True,
        eligible=False,
    )


class AdaptiveQualitySearchTests(unittest.TestCase):
    def setUp(self):
        self.spec = QualitySearchSpec(
            thresholds=QualityThresholds(vmaf_mean=95, vmaf_p5=93, ssim=0.99)
        )

    def test_finds_highest_passing_crf_and_checks_neighbors(self):
        calls = []

        def evaluate(crf):
            calls.append(crf)
            return point(
                crf,
                vmaf=110 - crf * 0.5,
                p5=108 - crf * 0.5,
                ssim=1.02 - crf * 0.001,
            )

        result = adaptive_quality_search(evaluate, self.spec)
        self.assertEqual(calls[:3], [18.0, 28.0, 38.0])
        self.assertEqual(result.selected.crf, 30.0)
        tested = {item.crf for item in result.points}
        self.assertIn(29.5, tested)
        self.assertIn(30.5, tested)
        self.assertFalse(result.exhaustive_fallback)
        self.assertLess(len(result.points), len(self.spec.grid()))

    def test_selects_upper_bound_when_all_anchors_pass(self):
        result = adaptive_quality_search(
            lambda crf: point(crf, vmaf=99, p5=98, ssim=0.999),
            self.spec,
        )
        self.assertEqual(result.selected.crf, 38.0)
        self.assertIn(37.5, {item.crf for item in result.points})

    def test_returns_none_when_even_lowest_crf_fails(self):
        result = adaptive_quality_search(
            lambda crf: point(crf, vmaf=90, p5=88, ssim=0.98),
            self.spec,
        )
        self.assertIsNone(result.selected)
        self.assertEqual(result.evaluation_order, [18.0, 28.0, 38.0])

    def test_non_monotonic_boundary_triggers_full_grid(self):
        def evaluate(crf):
            passing = crf <= 18.0 or crf >= 38.0
            return point(
                crf,
                vmaf=97 if passing else 90,
                p5=95 if passing else 88,
                ssim=0.995 if passing else 0.98,
            )

        result = adaptive_quality_search(evaluate, self.spec)
        self.assertTrue(result.exhaustive_fallback)
        self.assertEqual(len(result.points), len(self.spec.grid()))
        self.assertEqual(result.selected.crf, 38.0)
        self.assertTrue(result.monotonicity_violations)

    def test_rejects_invalid_search_grid(self):
        with self.assertRaises(ValueError):
            QualitySearchSpec(
                thresholds=self.spec.thresholds,
                crf_min=18,
                crf_max=38,
                crf_step=0.6,
            )


if __name__ == "__main__":
    unittest.main()
