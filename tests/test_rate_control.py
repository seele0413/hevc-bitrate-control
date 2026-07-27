import unittest

from hevc_lab.core.configs import default_aq_profile, get_mode_policy, v1_comparison_plan
from hevc_lab.core.models import CandidateResult, PacketBitrateStats, RateControlSettings
from hevc_lab.core.rate_control import (
    RateControlTrial,
    derive_vbv_settings,
    first_quality_preserving_trial,
    quality_is_preserved,
    vbv_ratio_candidates,
)
from hevc_lab.core.search import QualityThresholds
from hevc_lab.encoders import build_x265_params


def candidate(vmaf=96, p5=94, ssim=0.995):
    return CandidateResult(
        name="optimized",
        title="optimized",
        description="",
        output_path="candidate.mp4",
        x265_params="",
        crf=29,
        preset="medium",
        bitrate_bps=200_000,
        file_size_bytes=1,
        vmaf_mean=vmaf,
        vmaf_p5=p5,
        ssim=ssim,
        encode_seconds=1,
        encode_speed_x=2,
        quality_pass=True,
        speed_pass=True,
        eligible=True,
    )


class RateControlTests(unittest.TestCase):
    def test_vbv_requires_maxrate_and_bufsize_together(self):
        with self.assertRaises(ValueError):
            RateControlSettings(vbv_maxrate_kbps=100)

    def test_derives_capped_crf_vbv_from_natural_average(self):
        settings = derive_vbv_settings(
            average_bitrate_bps=200_000,
            peak_ratio=1.5,
            buffer_seconds=3,
        )
        self.assertEqual(settings.vbv_maxrate_kbps, 300)
        self.assertEqual(settings.vbv_bufsize_kbits, 900)
        self.assertIn("vbv-maxrate=300", settings.x265_params())
        self.assertIn("const-vbv=1", settings.x265_params())

    def test_mode_ratios_relax_from_strict_to_wide(self):
        ratios = vbv_ratio_candidates(get_mode_policy("balanced"))
        self.assertEqual(ratios[0], 1.5)
        self.assertEqual(ratios[-1], 2.5)
        self.assertEqual(ratios, tuple(sorted(ratios)))
        with self.assertRaises(ValueError):
            vbv_ratio_candidates(
                get_mode_policy("balanced"),
                maximum_ratio=1.25,
            )

    def test_quality_guard_requires_absolute_thresholds_and_delta(self):
        thresholds = QualityThresholds(95, 93, 0.99)
        uncapped = candidate(vmaf=96)
        self.assertTrue(
            quality_is_preserved(candidate(vmaf=95.2), uncapped, thresholds, 1.0)
        )
        self.assertFalse(
            quality_is_preserved(candidate(vmaf=94.99), uncapped, thresholds, 1.0)
        )
        self.assertFalse(
            quality_is_preserved(candidate(vmaf=95.2, p5=92.9), uncapped, thresholds, 1.0)
        )

    def test_encoder_params_include_vbv_without_changing_interframe_config(self):
        plan = v1_comparison_plan("balanced")
        settings = RateControlSettings(300, 900)
        params = build_x265_params(plan.optimized, 25, settings)
        self.assertIn("keyint=250", params)
        self.assertIn("vbv-maxrate=300", params)
        self.assertIn("vbv-bufsize=900", params)

    def test_encoder_params_can_combine_vbv_and_aq(self):
        plan = v1_comparison_plan("balanced")
        params = build_x265_params(
            plan.optimized,
            25,
            RateControlSettings(300, 900),
            default_aq_profile(),
        )
        self.assertIn("vbv-maxrate=300", params)
        self.assertIn("aq-mode=2", params)
        self.assertIn("aq-strength=1.0", params)

    def test_selection_requires_quality_and_real_bitrate_improvement(self):
        stats = PacketBitrateStats(
            packet_count=1,
            packet_bytes=1,
            duration_seconds=1,
            average_bitrate_bps=100,
            window_seconds=1,
            window_bitrates_bps=(100,),
            peak_window_bitrate_bps=100,
            p95_window_bitrate_bps=100,
        )
        settings = RateControlSettings(300, 900)
        no_gain = RateControlTrial(
            peak_ratio=1.5,
            settings=settings,
            candidate=candidate(),
            packet_stats=stats,
            vmaf_delta=0,
            quality_preserved=True,
            bitrate_beneficial=False,
        )
        useful = RateControlTrial(
            peak_ratio=1.75,
            settings=settings,
            candidate=candidate(),
            packet_stats=stats,
            vmaf_delta=0,
            quality_preserved=True,
            bitrate_beneficial=True,
        )
        self.assertIs(first_quality_preserving_trial([no_gain, useful]), useful)


if __name__ == "__main__":
    unittest.main()
