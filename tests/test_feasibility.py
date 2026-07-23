import unittest
from pathlib import Path

from hevc_lab.core.configs import get_mode_policy
from hevc_lab.core.feasibility import (
    ContinuityValidation,
    assess_video_continuity,
    evaluate_feasibility,
)
from hevc_lab.core.matching import EqualQualityMatchResult, EqualQualityPair
from hevc_lab.core.models import CandidateResult, ReferenceArtifact, VideoInfo


def video(path="video.mp4", duration=0.2):
    return VideoInfo(
        path=Path(path),
        codec="hevc",
        width=1920,
        height=1080,
        fps=20.0,
        duration_seconds=duration,
        video_bitrate_bps=1_000_000,
        file_size_bytes=1,
        pixel_format="yuv420p",
    )


def reference():
    return ReferenceArtifact(
        input_path=Path("input.mp4"),
        input_sha256="abc",
        cache_key="key",
        requested_start_seconds=0.0,
        requested_duration_seconds=0.2,
        effective_duration_seconds=0.2,
        expected_frame_count=4,
        frame_count=4,
        video=video("reference.mkv"),
        timestamp_summary={},
        manifest_path=Path("reference.json"),
    )


def candidate(name, bitrate, speed=1.5):
    return CandidateResult(
        name=name,
        title=name,
        description="",
        output_path=f"{name}.mp4",
        x265_params="",
        crf=25.0,
        preset="medium",
        bitrate_bps=bitrate,
        file_size_bytes=1,
        vmaf_mean=95.2,
        vmaf_p5=93.5,
        ssim=0.993,
        encode_seconds=10,
        encode_speed_x=speed,
        quality_pass=True,
        speed_pass=True,
        eligible=True,
    )


def matched(baseline_bitrate, optimized_bitrate, speed=1.5):
    baseline = candidate("baseline", baseline_bitrate)
    optimized = candidate("optimized", optimized_bitrate, speed=speed)
    pair = EqualQualityPair(
        baseline=baseline,
        optimized=optimized,
        vmaf_delta=0.1,
        vmaf_p5_delta=0.2,
        ssim_delta=0.0001,
        algorithm_saving_pct=(1.0 - optimized_bitrate / baseline_bitrate) * 100.0,
    )
    return EqualQualityMatchResult(
        status="matched",
        max_vmaf_delta=1.0,
        pair=pair,
        reason="matched",
        baseline_boundary_crfs=(25.0,),
        optimized_boundary_crfs=(25.0,),
        evaluated_pair_count=1,
        qualifying_pair_count=1,
    )


def unmatched():
    return EqualQualityMatchResult(
        status="insufficient_evidence",
        max_vmaf_delta=1.0,
        pair=None,
        reason="没有画质合格边界点",
        baseline_boundary_crfs=(),
        optimized_boundary_crfs=(),
        evaluated_pair_count=0,
        qualifying_pair_count=0,
    )


def continuity(passed=True):
    return ContinuityValidation(
        checked=True,
        passed=passed,
        output_path="optimized.mp4",
        reason="连续" if passed else "不连续",
        checks={"decoded_without_error": passed},
        metrics={"output_frame_count": 4.0},
    )


class ContinuityAssessmentTests(unittest.TestCase):
    def test_complete_cfr_output_passes(self):
        result = assess_video_continuity(
            reference(),
            video(),
            [0.0, 0.05, 0.10, 0.15],
            decoded_without_error=True,
        )
        self.assertTrue(result.passed)
        self.assertTrue(all(result.checks.values()))

    def test_large_timestamp_gap_is_rejected(self):
        result = assess_video_continuity(
            reference(),
            video(),
            [0.0, 0.05, 0.15, 0.20],
            decoded_without_error=True,
        )
        self.assertFalse(result.passed)
        self.assertFalse(result.checks["max_frame_gap"])

    def test_frame_loss_or_decode_error_is_rejected(self):
        result = assess_video_continuity(
            reference(),
            video(),
            [0.0, 0.05, 0.10],
            decoded_without_error=False,
        )
        self.assertFalse(result.checks["decoded_without_error"])
        self.assertFalse(result.checks["frame_count_match"])
        self.assertFalse(result.passed)


class FeasibilityConclusionTests(unittest.TestCase):
    def test_balanced_mode_passes_algorithm_continuity_and_deployment(self):
        result = evaluate_feasibility(
            matched(1_000_000, 900_000, speed=1.2),
            get_mode_policy("balanced"),
            source_video_bitrate_bps=1_000_000,
            continuity=continuity(),
        )
        self.assertTrue(result.algorithm.passed)
        self.assertEqual(result.algorithm.decision, "effective")
        self.assertTrue(result.software_continuity.passed)
        self.assertEqual(
            result.software_continuity.decision,
            "realtime_continuous_headroom",
        )
        self.assertTrue(result.deployment.passed)

    def test_zero_saving_fails_even_in_conservative_mode(self):
        result = evaluate_feasibility(
            matched(1_000_000, 1_000_000),
            get_mode_policy("conservative"),
            1_000_000,
            continuity(),
        )
        self.assertFalse(result.algorithm.passed)
        self.assertFalse(result.algorithm.checks["strictly_positive_saving"])

    def test_point_nine_seven_speed_passes_with_buffer_but_lower_fails(self):
        passing = evaluate_feasibility(
            matched(1_000_000, 900_000, speed=0.97),
            get_mode_policy("balanced"),
            1_000_000,
            continuity(),
        )
        failing = evaluate_feasibility(
            matched(1_000_000, 900_000, speed=0.969),
            get_mode_policy("balanced"),
            1_000_000,
            continuity(),
        )
        self.assertTrue(passing.software_continuity.passed)
        self.assertEqual(
            passing.software_continuity.decision,
            "near_realtime_continuous",
        )
        self.assertFalse(failing.software_continuity.passed)
        self.assertEqual(failing.software_continuity.decision, "offline_only")

    def test_continuity_requires_video_validation_even_when_speed_is_high(self):
        result = evaluate_feasibility(
            matched(1_000_000, 900_000, speed=2.0),
            get_mode_policy("balanced"),
            1_000_000,
            continuity(False),
        )
        self.assertFalse(result.software_continuity.passed)
        self.assertEqual(result.software_continuity.decision, "not_continuous")

    def test_aggressive_mode_accepts_offline_speed_when_video_is_continuous(self):
        result = evaluate_feasibility(
            matched(1_000_000, 850_000, speed=0.4),
            get_mode_policy("aggressive"),
            1_000_000,
            continuity(),
        )
        self.assertTrue(result.software_continuity.passed)
        self.assertEqual(
            result.software_continuity.decision,
            "offline_continuous",
        )
        self.assertFalse(
            result.software_continuity.checks["speed_gate_enabled"]
        )
        self.assertEqual(
            result.software_continuity.metrics["speed_tier"],
            "offline",
        )

    def test_deployment_source_gate_is_independent_from_algorithm_gate(self):
        result = evaluate_feasibility(
            matched(1_000_000, 960_000, speed=2.0),
            get_mode_policy("balanced"),
            2_000_000,
            continuity(),
        )
        self.assertFalse(result.algorithm.passed)
        self.assertTrue(result.deployment.passed)
        self.assertEqual(
            result.deployment.decision,
            "hardware_validation_recommended",
        )

    def test_missing_pair_returns_insufficient_evidence_and_passthrough(self):
        result = evaluate_feasibility(
            unmatched(),
            get_mode_policy("balanced"),
            1_000_000,
            ContinuityValidation.not_checked("no pair"),
        )
        self.assertEqual(result.algorithm.decision, "insufficient_evidence")
        self.assertEqual(result.software_continuity.decision, "not_checked")
        self.assertEqual(result.deployment.decision, "passthrough")

    def test_invalid_source_bitrate_forces_passthrough(self):
        result = evaluate_feasibility(
            matched(1_000_000, 900_000),
            get_mode_policy("balanced"),
            0.0,
            continuity(),
        )
        self.assertFalse(result.deployment.passed)
        self.assertFalse(result.deployment.checks["source_bitrate_available"])


if __name__ == "__main__":
    unittest.main()
