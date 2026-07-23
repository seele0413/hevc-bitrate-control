import unittest
from pathlib import Path

from hevc_lab.core.configs import denoise_policy_for_mode
from hevc_lab.core.denoise import decide_denoise_selection
from hevc_lab.core.matching import EqualQualityMatchResult, EqualQualityPair
from hevc_lab.core.models import (
    CandidateResult,
    DenoiseSettings,
    DenoiseStrength,
    ROIRegion,
)
from hevc_lab.core.roi import (
    RegionQualityMetrics,
    compare_region_quality,
    load_roi_settings,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROI_CONFIG = PROJECT_ROOT / "configs" / "camera-entrance-roi.json"


def candidate(bitrate=100_000, speed_pass=True):
    return CandidateResult(
        name="denoise",
        title="denoise",
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
        encode_speed_x=2 if speed_pass else 0.5,
        quality_pass=True,
        speed_pass=speed_pass,
        eligible=speed_pass,
    )


def matched(control_bitrate=100_000, denoise_bitrate=90_000, speed_pass=True):
    control = candidate(control_bitrate)
    denoise = candidate(denoise_bitrate, speed_pass=speed_pass)
    return EqualQualityMatchResult(
        status="matched",
        max_vmaf_delta=1.0,
        pair=EqualQualityPair(
            baseline=control,
            optimized=denoise,
            vmaf_delta=0.1,
            vmaf_p5_delta=0.1,
            ssim_delta=0.0001,
            algorithm_saving_pct=(control_bitrate - denoise_bitrate)
            / control_bitrate
            * 100,
        ),
        reason="matched",
        baseline_boundary_crfs=(25,),
        optimized_boundary_crfs=(25,),
        evaluated_pair_count=1,
        qualifying_pair_count=1,
    )


def local_quality(role="critical", vmaf_drop=0.1, p5_drop=0.2, ssim_drop=0.001):
    region = ROIRegion("region", "region", role, 0, 0, 32, 32)
    control = RegionQualityMetrics(96, 94, 0.999)
    denoise = RegionQualityMetrics(
        96 - vmaf_drop,
        94 - p5_drop,
        0.999 - ssim_drop,
    )
    return compare_region_quality(region, control, denoise)


class DenoiseSettingsTests(unittest.TestCase):
    def test_strength_builds_explicit_hqdn3d_or_bypass(self):
        strength = DenoiseStrength(1.2, 0.9, 2.0, 1.5)
        self.assertEqual(strength.filter_expression(), "hqdn3d=1.2:0.9:2:1.5")
        self.assertTrue(strength.enabled)
        bypass = DenoiseStrength(0, 0, 0, 0)
        self.assertEqual(bypass.filter_expression(), "null")
        self.assertFalse(bypass.enabled)
        with self.assertRaises(ValueError):
            DenoiseStrength(-0.1, 0, 0, 0)

    def test_three_modes_increase_background_strength_and_bypass_evidence(self):
        policies = [
            denoise_policy_for_mode(mode)
            for mode in ("conservative", "balanced", "aggressive")
        ]
        self.assertEqual(
            [item.normal.luma_spatial for item in policies],
            [0.8, 1.2, 1.8],
        )
        self.assertEqual(
            [item.discard.luma_temporal for item in policies],
            [1.8, 3.0, 4.0],
        )
        self.assertTrue(all(not item.evidence.enabled for item in policies))

    def test_filter_graph_overlays_discard_critical_then_evidence(self):
        roi = load_roi_settings(ROI_CONFIG, "balanced")
        settings = DenoiseSettings(roi, denoise_policy_for_mode("balanced"))
        roles = [region.role for region in settings.overlay_regions()]
        self.assertEqual(roles[0], "discard")
        self.assertEqual(roles[1:4], ["critical"] * 3)
        self.assertEqual(roles[-1], "evidence")
        graph = settings.filter_complex()
        self.assertIn("[0:v]split=6", graph)
        self.assertIn("[base_src]hqdn3d=1.2:0.9:2:1.5", graph)
        self.assertIn("crop=384:112:1504:16,null[patch_4]", graph)
        self.assertTrue(graph.endswith("format=yuv420p[denoised]"))


class DenoiseSelectionTests(unittest.TestCase):
    def test_all_checks_select_denoise(self):
        decision = decide_denoise_selection(
            matched(),
            [local_quality("critical"), local_quality("evidence")],
        )
        self.assertTrue(decision.selected)
        self.assertEqual(decision.decision, "denoise_selected")

    def test_local_quality_failure_falls_back(self):
        decision = decide_denoise_selection(
            matched(),
            [local_quality("critical", vmaf_drop=0.6)],
        )
        self.assertFalse(decision.selected)
        self.assertFalse(decision.checks["critical_regions"])

    def test_bitrate_increase_or_speed_failure_falls_back(self):
        bitrate = decide_denoise_selection(
            matched(control_bitrate=100_000, denoise_bitrate=101_000),
            [local_quality("critical")],
        )
        speed = decide_denoise_selection(
            matched(speed_pass=False),
            [local_quality("critical")],
        )
        self.assertFalse(bitrate.checks["average_bitrate_strictly_lower"])
        self.assertFalse(speed.checks["denoise_speed"])


if __name__ == "__main__":
    unittest.main()
