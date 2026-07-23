import unittest

from hevc_lab.core.aq import AdaptiveQuantizationTrial, select_best_aq_trial
from hevc_lab.core.configs import aq_profiles_for_mode, default_aq_profile
from hevc_lab.core.matching import EqualQualityMatchResult, EqualQualityPair
from hevc_lab.core.models import AdaptiveQuantizationSettings, CandidateResult
from hevc_lab.core.search import QualitySearchResult, QualitySearchSpec, QualityThresholds


def candidate(bitrate=100_000):
    return CandidateResult(
        name="aq",
        title="aq",
        description="",
        output_path="candidate.mp4",
        x265_params="",
        crf=25,
        preset="medium",
        bitrate_bps=bitrate,
        file_size_bytes=1,
        vmaf_mean=95.5,
        vmaf_p5=93.5,
        ssim=0.995,
        encode_seconds=1,
        encode_speed_x=2,
        quality_pass=True,
        speed_pass=True,
        eligible=True,
    )


def trial(saving):
    profile = aq_profiles_for_mode("balanced")[0]
    baseline = candidate(100_000)
    optimized = candidate(100_000 * (1 - saving / 100))
    search = QualitySearchResult(
        spec=QualitySearchSpec(QualityThresholds(95, 93, 0.99)),
        points=[optimized],
        evaluation_order=[25],
        selected=optimized,
        monotonicity_violations=[],
        exhaustive_fallback=False,
    )
    match = EqualQualityMatchResult(
        status="matched",
        max_vmaf_delta=1,
        pair=EqualQualityPair(
            baseline=baseline,
            optimized=optimized,
            vmaf_delta=0,
            vmaf_p5_delta=0,
            ssim_delta=0,
            algorithm_saving_pct=saving,
        ),
        reason="matched",
        baseline_boundary_crfs=(25,),
        optimized_boundary_crfs=(25,),
        evaluated_pair_count=1,
        qualifying_pair_count=1,
    )
    return AdaptiveQuantizationTrial(profile, search, match)


class AdaptiveQuantizationTests(unittest.TestCase):
    def test_settings_validate_and_build_explicit_x265_params(self):
        profile = default_aq_profile()
        self.assertEqual(profile.aq_mode, 2)
        self.assertEqual(profile.aq_strength, 1.0)
        self.assertIn("aq-mode=2", profile.x265_params())
        self.assertIn("qg-size=32", profile.x265_params())
        self.assertIn("aq-motion=0", profile.x265_params())
        with self.assertRaises(ValueError):
            AdaptiveQuantizationSettings("bad", "bad", "", 5, 1, 32)
        with self.assertRaises(ValueError):
            AdaptiveQuantizationSettings("bad", "bad", "", 2, 3.1, 32)

    def test_three_modes_reuse_profiles_with_pareto_strength_and_granularity(self):
        mode_profiles = [
            aq_profiles_for_mode(mode)
            for mode in ("conservative", "balanced", "aggressive")
        ]
        self.assertEqual([[item.aq_mode for item in row] for row in mode_profiles], [[3, 4]] * 3)
        self.assertEqual([row[0].aq_strength for row in mode_profiles], [0.8, 1.0, 1.2])
        self.assertEqual([row[0].qg_size for row in mode_profiles], [32, 16, 16])
        self.assertTrue(all(not item.aq_motion for row in mode_profiles for item in row))

    def test_selection_requires_positive_equal_quality_bitrate_gain(self):
        weak = trial(1.0)
        strong = trial(3.0)
        self.assertIs(select_best_aq_trial([weak, strong]), strong)
        self.assertIsNone(select_best_aq_trial([trial(0.0)]))
        self.assertIsNone(select_best_aq_trial([strong], min_saving_pct=3.0))


if __name__ == "__main__":
    unittest.main()
