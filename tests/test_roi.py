import json
import tempfile
import unittest
from pathlib import Path

from hevc_lab.core.configs import roi_quantization_policy
from hevc_lab.core.matching import EqualQualityMatchResult, EqualQualityPair
from hevc_lab.core.models import CandidateResult, ROIRegion
from hevc_lab.core.roi import (
    RegionQualityMetrics,
    compare_region_quality,
    decide_roi_selection,
    load_roi_settings,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "camera-entrance-roi.json"


def candidate(bitrate=100_000, speed_pass=True):
    return CandidateResult(
        name="roi",
        title="roi",
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


def matched(control_bitrate=100_000, roi_bitrate=90_000, speed_pass=True):
    control = candidate(control_bitrate)
    roi = candidate(roi_bitrate, speed_pass=speed_pass)
    return EqualQualityMatchResult(
        status="matched",
        max_vmaf_delta=1.0,
        pair=EqualQualityPair(
            baseline=control,
            optimized=roi,
            vmaf_delta=0.0,
            vmaf_p5_delta=0.0,
            ssim_delta=0.0,
            algorithm_saving_pct=(control_bitrate - roi_bitrate) / control_bitrate * 100,
        ),
        reason="matched",
        baseline_boundary_crfs=(25,),
        optimized_boundary_crfs=(25,),
        evaluated_pair_count=1,
        qualifying_pair_count=1,
    )


def region_quality(role="critical", vmaf_drop=0.1, p5_drop=0.2, ssim_drop=0.001):
    region = ROIRegion("region", "region", role, 0, 0, 32, 32)
    control = RegionQualityMetrics(96, 94, 0.999)
    roi = RegionQualityMetrics(
        96 - vmaf_drop,
        94 - p5_drop,
        0.999 - ssim_drop,
    )
    return compare_region_quality(region, control, roi)


class ROIConfigurationTests(unittest.TestCase):
    def test_default_camera_config_and_resolution_validation(self):
        settings = load_roi_settings(DEFAULT_CONFIG, "balanced")
        self.assertEqual(settings.camera_id, "entrance-camera-01")
        self.assertEqual((settings.reference_width, settings.reference_height), (1920, 1080))
        self.assertEqual(len(settings.regions), 5)
        settings.validate_input(1920, 1080)
        with self.assertRaisesRegex(ValueError, "不允许静默缩放"):
            settings.validate_input(1280, 720)

    def test_invalid_size_bounds_and_duplicate_ids_are_rejected(self):
        payload = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        cases = []
        bad_size = json.loads(json.dumps(payload))
        bad_size["regions"][0]["width"] = 0
        cases.append(bad_size)
        out_of_bounds = json.loads(json.dumps(payload))
        out_of_bounds["regions"][0]["width"] = 2000
        cases.append(out_of_bounds)
        duplicate = json.loads(json.dumps(payload))
        duplicate["regions"][1]["id"] = duplicate["regions"][0]["id"]
        cases.append(duplicate)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roi.json"
            for item in cases:
                path.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_roi_settings(path)

    def test_regions_report_effective_outward_16_pixel_alignment(self):
        region = ROIRegion("unaligned", "unaligned", "critical", 17, 31, 18, 34)
        self.assertEqual(region.aligned_rect(100, 100), (16, 16, 32, 64))
        edge = ROIRegion("edge", "edge", "critical", 96, 96, 4, 4)
        self.assertEqual(edge.aligned_rect(100, 100), (96, 96, 4, 4))

    def test_mode_qp_and_qoffset_conversion_follow_design(self):
        policies = [
            roi_quantization_policy(mode)
            for mode in (
                "conservative",
                "balanced",
                "aggressive",
                "aggressive_plus",
                "aggressive_plus_plus",
                "aggressive_plus_plus_plus",
            )
        ]
        self.assertEqual(
            [(item.critical, item.evidence, item.normal, item.discard) for item in policies],
            [
                (-1, -2, 4, 6),
                (-2, -2, 5, 8),
                (-4, -3, 5, 8),
                (-4, -3, 7, 12),
                (-4, -3, 9, 16),
                (-4, -3, 11, 20),
            ],
        )
        self.assertAlmostEqual(policies[1].qoffset("critical"), -2 / 51)
        self.assertEqual(policies[1].qoffset_expression("discard"), "8/51")

    def test_filter_order_preserves_priority_and_appends_normal_fallback(self):
        settings = load_roi_settings(DEFAULT_CONFIG, "balanced")
        entries = settings.filter_entries()
        self.assertEqual(entries[0][0].role, "evidence")
        self.assertEqual([item[0].role for item in entries[1:4]], ["critical"] * 3)
        self.assertEqual(entries[-2][0].role, "discard")
        self.assertEqual(entries[-1][0].region_id, "__normal_fallback__")
        chain = settings.filter_chain()
        self.assertTrue(chain.startswith("addroi=x=1504:y=16:w=384:h=112:qoffset=-2/51:clear=1"))
        self.assertTrue(chain.endswith("addroi=x=0:y=0:w=1920:h=1080:qoffset=5/51"))


class ROISelectionTests(unittest.TestCase):
    def test_global_pass_but_critical_region_failure_falls_back(self):
        local = region_quality("critical", vmaf_drop=0.6, p5_drop=0.2)
        decision = decide_roi_selection(matched(), [local])
        self.assertFalse(decision.selected)
        self.assertFalse(decision.checks["critical_regions"])
        self.assertEqual(decision.decision, "no_roi_fallback")

    def test_local_quality_pass_but_bitrate_increase_falls_back(self):
        decision = decide_roi_selection(
            matched(control_bitrate=100_000, roi_bitrate=101_000),
            [region_quality("critical"), region_quality("evidence")],
        )
        self.assertFalse(decision.selected)
        self.assertFalse(decision.checks["average_bitrate_strictly_lower"])

    def test_all_global_local_speed_and_bitrate_checks_select_roi(self):
        decision = decide_roi_selection(
            matched(),
            [region_quality("critical"), region_quality("evidence")],
        )
        self.assertTrue(decision.selected)
        self.assertTrue(all(decision.checks.values()))
        self.assertEqual(decision.decision, "roi_selected")

    def test_evidence_ssim_drop_is_independent_from_critical_limits(self):
        comparison = region_quality("evidence", vmaf_drop=2, p5_drop=3, ssim_drop=0.0021)
        self.assertFalse(comparison.quality_pass)


if __name__ == "__main__":
    unittest.main()
