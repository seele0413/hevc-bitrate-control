import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hevc_lab.core.configs import denoise_policy_for_mode, multi_encode_strategies
from hevc_lab.core.models import (
    CandidateResult,
    DenoiseSettings,
    MultiEncodeStrategy,
    ReferenceArtifact,
    ROIRegion,
    Toolchain,
    VideoInfo,
)
from hevc_lab.core.roi import ROIRegionQuality, RegionQualityMetrics, load_roi_settings
from hevc_lab.core.search import (
    QualitySearchResult,
    QualitySearchSpec,
    QualityThresholds,
)
from hevc_lab.encoders.x265 import (
    combined_roi_denoise_filter,
    encode_default_h264,
    encode_default_x265,
)
from hevc_lab.multi_encode import (
    _composite_strategy,
    _fixed_hevc_strategy,
    _select_short_candidate,
    bitrate_saving_vs_default_pct,
    run_multi_encode,
)
from hevc_lab.reports.multi_writer import write_multi_encode_reports


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROI_CONFIG = PROJECT_ROOT / "configs" / "camera-entrance-roi.json"


def video(path: Path, bitrate=500_000.0) -> VideoInfo:
    return VideoInfo(
        path=path,
        codec="ffv1",
        width=1920,
        height=1080,
        fps=20.0,
        duration_seconds=60.0,
        video_bitrate_bps=bitrate,
        file_size_bytes=1_000,
        pixel_format="yuv420p",
    )


def reference(path: Path, duration=60.0) -> ReferenceArtifact:
    item = video(path)
    item = VideoInfo(
        **{
            **item.to_dict(),
            "path": path,
            "duration_seconds": duration,
        }
    )
    return ReferenceArtifact(
        input_path=path,
        input_sha256="input-hash",
        cache_key=f"reference-{duration}",
        requested_start_seconds=0.0,
        requested_duration_seconds=duration,
        effective_duration_seconds=duration,
        expected_frame_count=round(duration * 20),
        frame_count=round(duration * 20),
        video=item,
        timestamp_summary={},
        manifest_path=path.with_suffix(".json"),
    )


def candidate(crf, passed, path="candidate.mp4", cache_hit=False, bitrate=400_000.0):
    return CandidateResult(
        name="optimized",
        title="综合策略",
        description="test",
        output_path=path,
        x265_params="test=1",
        crf=crf,
        preset="medium",
        bitrate_bps=bitrate,
        file_size_bytes=100,
        vmaf_mean=91.0 if passed else 89.0,
        vmaf_p5=89.0 if passed else 87.0,
        ssim=0.981 if passed else 0.979,
        encode_seconds=10.0,
        encode_speed_x=1.2,
        quality_pass=passed,
        speed_pass=True,
        eligible=passed,
        cache_hit=cache_hit,
    )


def public_strategy(public_mode):
    return next(
        item for item in multi_encode_strategies() if item.public_mode == public_mode
    )


def historical_strategy(public_mode):
    if public_mode == "general":
        return MultiEncodeStrategy(
            public_mode="general",
            strategy_id="generic_no_roi",
            title="通用无 ROI 方案",
            description="historical general",
            source_mode="aggressive",
            strategy_generation="v1.4_general_no_roi",
            effective_preset="medium",
            roi_enabled=False,
            denoise_enabled=False,
            target_vmaf=83.0,
            target_vmaf_p5=80.0,
            target_ssim=0.950,
        )
    if public_mode == "roi":
        return MultiEncodeStrategy(
            public_mode="roi",
            strategy_id="budget_neutral_roi",
            title="预算中性 ROI 方案",
            description="historical roi",
            source_mode="aggressive",
            strategy_generation="v1.4_budget_neutral_roi",
            effective_preset="medium",
            roi_enabled=True,
            denoise_enabled=False,
            target_vmaf=83.0,
            target_vmaf_p5=80.0,
            target_ssim=0.950,
            budget_reference="generic_no_roi",
            budget_neutral_required=True,
            roi_quality_required=True,
        )
    raise ValueError(public_mode)


def region_quality(improved=True):
    region = ROIRegion(
        region_id="door",
        title="入口门",
        role="critical",
        x=0,
        y=0,
        width=64,
        height=64,
    )
    control = RegionQualityMetrics(vmaf_mean=90.0, vmaf_p5=88.0, ssim=0.980)
    roi = RegionQualityMetrics(
        vmaf_mean=90.2 if improved else 89.9,
        vmaf_p5=88.1 if improved else 87.9,
        ssim=0.981 if improved else 0.979,
    )
    return (
        ROIRegionQuality(
            region=region,
            control=control,
            roi=roi,
            vmaf_drop=control.vmaf_mean - roi.vmaf_mean,
            vmaf_p5_drop=control.vmaf_p5 - roi.vmaf_p5,
            ssim_drop=control.ssim - roi.ssim,
            quality_pass=improved,
            reason="test",
        ),
    )


class CompositeFilterTests(unittest.TestCase):
    def test_denoise_runs_before_addroi(self):
        roi = load_roi_settings(ROI_CONFIG, "balanced")
        denoise = DenoiseSettings(roi, denoise_policy_for_mode("balanced"))
        graph = combined_roi_denoise_filter(roi, denoise)
        self.assertLess(graph.index("hqdn3d"), graph.index("addroi="))
        self.assertTrue(graph.endswith("[filtered]"))

    def test_combination_rejects_different_mode_policies(self):
        roi = load_roi_settings(ROI_CONFIG, "balanced")
        aggressive_roi = load_roi_settings(ROI_CONFIG, "aggressive")
        denoise = DenoiseSettings(
            aggressive_roi,
            denoise_policy_for_mode("aggressive"),
        )
        with self.assertRaises(ValueError):
            combined_roi_denoise_filter(roi, denoise)


class DefaultEncoderTests(unittest.TestCase):
    def test_default_encoder_passes_no_custom_encoding_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = video(root / "input.mkv")
            toolchain = Toolchain(root / "ffmpeg", root / "ffprobe", root / "vmaf.json")
            completed = SimpleNamespace(stdout="", stderr="")
            with patch(
                "hevc_lab.encoders.x265.run_process",
                return_value=completed,
            ) as mocked:
                encode_default_x265(
                    toolchain,
                    source,
                    root / "output.mp4",
                    root / "encode.log",
                )
            command = [str(item) for item in mocked.call_args.args[0]]
            self.assertIn("libx265", command)
            self.assertNotIn("-crf", command)
            self.assertNotIn("-preset", command)
            self.assertNotIn("-x265-params", command)
            self.assertNotIn("-vf", command)
            self.assertNotIn("-filter_complex", command)

    def test_h264_native_encoder_passes_no_custom_encoding_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = video(root / "input.mkv")
            toolchain = Toolchain(root / "ffmpeg", root / "ffprobe", root / "vmaf.json")
            completed = SimpleNamespace(stdout="", stderr="")
            with patch(
                "hevc_lab.encoders.x265.run_process",
                return_value=completed,
            ) as mocked:
                encode_default_h264(
                    toolchain,
                    source,
                    root / "output.mp4",
                    root / "encode.log",
                )
            command = [str(item) for item in mocked.call_args.args[0]]
            self.assertIn("libx264", command)
            self.assertNotIn("-crf", command)
            self.assertNotIn("-preset", command)
            self.assertNotIn("-x264-params", command)
            self.assertNotIn("-vf", command)
            self.assertNotIn("-filter_complex", command)


class MultiEncodeDecisionTests(unittest.TestCase):
    def test_saving_keeps_positive_zero_and_negative_values(self):
        self.assertAlmostEqual(bitrate_saving_vs_default_pct(100, 80), 20.0)
        self.assertAlmostEqual(bitrate_saving_vs_default_pct(100, 100), 0.0)
        self.assertAlmostEqual(bitrate_saving_vs_default_pct(100, 120), -20.0)
        with self.assertRaises(ValueError):
            bitrate_saving_vs_default_pct(0, 80)

    def test_target_selection_prefers_lowest_bitrate_inside_range(self):
        selected, meta = _select_short_candidate(
            [
                candidate(24.0, True, bitrate=88.0),
                candidate(24.5, True, bitrate=86.0),
                candidate(25.0, True, bitrate=84.0),
            ],
            default_bitrate_bps=100.0,
            target_min_pct=10.0,
            target_max_pct=15.0,
        )
        self.assertEqual(selected.crf, 24.5)
        self.assertTrue(meta["target_saving_met"])
        self.assertEqual(meta["saving_target_status"], "met")
        self.assertEqual(
            meta["selection_reason"],
            "target_range_lowest_bitrate_quality_pass",
        )

    def test_target_selection_reports_below_target_without_faking_success(self):
        selected, meta = _select_short_candidate(
            [
                candidate(24.0, True, bitrate=96.0),
                candidate(24.5, True, bitrate=92.0),
            ],
            default_bitrate_bps=100.0,
            target_min_pct=10.0,
            target_max_pct=15.0,
        )
        self.assertEqual(selected.crf, 24.5)
        self.assertFalse(meta["target_saving_met"])
        self.assertEqual(meta["saving_target_status"], "below_target")

    def test_target_selection_reports_above_target_without_faking_success(self):
        selected, meta = _select_short_candidate(
            [
                candidate(24.0, True, bitrate=60.0),
                candidate(24.5, True, bitrate=65.0),
            ],
            default_bitrate_bps=100.0,
            target_min_pct=20.0,
            target_max_pct=30.0,
        )
        self.assertEqual(selected.crf, 24.5)
        self.assertFalse(meta["target_saving_met"])
        self.assertEqual(meta["saving_target_status"], "above_target")

    def test_target_selection_rejects_quality_failed_points(self):
        selected, meta = _select_short_candidate(
            [candidate(24.0, False, bitrate=80.0)],
            default_bitrate_bps=100.0,
            target_min_pct=10.0,
            target_max_pct=15.0,
        )
        self.assertIsNone(selected)
        self.assertEqual(meta["saving_target_status"], "no_quality_candidate")

    def test_full_validation_only_steps_down_current_mode_crf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_source = video(root / "input.mp4")
            short_ref = reference(root / "short.mkv", 12.0)
            full_ref = reference(root / "full.mkv", 60.0)
            thresholds = QualityThresholds(90.0, 88.0, 0.980)
            selected = candidate(26.0, True)
            search = QualitySearchResult(
                spec=QualitySearchSpec(thresholds),
                points=[selected],
                evaluation_order=[26.0],
                selected=selected,
                monotonicity_violations=[],
                exhaustive_fallback=False,
            )
            attempts = [candidate(26.0, False), candidate(25.5, True)]
            output_info = video(root / "generic_no_roi.mp4", 350_000.0)
            toolchain = Toolchain(
                root / "ffmpeg",
                root / "ffprobe",
                root / "vmaf.json",
            )
            stages = []
            with patch(
                "hevc_lab.multi_encode.run_scheme_quality_search",
                return_value=search,
            ), patch(
                "hevc_lab.multi_encode.evaluate_scheme_crf",
                side_effect=attempts,
            ) as evaluate, patch(
                "hevc_lab.multi_encode._publish_candidate",
                return_value=False,
            ), patch(
                "hevc_lab.multi_encode.probe_video",
                return_value=output_info,
            ):
                result = _composite_strategy(
                    toolchain,
                    input_source,
                    short_ref,
                    full_ref,
                    ROI_CONFIG,
                    root,
                    historical_strategy("general"),
                    default_bitrate_bps=500_000.0,
                    progress_callback=stages.append,
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["selected_crf"], 25.5)
            self.assertIsNone(result["target_saving_met"])
            self.assertEqual(result["saving_target_status"], "not_applicable")
            self.assertEqual(stages, ["searching_general", "validating_general"])
            self.assertEqual(
                [call.kwargs["crf"] for call in evaluate.call_args_list],
                [26.0, 25.5],
            )

    def test_v1_4_general_strategy_is_medium_without_roi_or_denoise(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_source = video(root / "input.mp4")
            short_ref = reference(root / "short.mkv", 12.0)
            full_ref = reference(root / "full.mkv", 60.0)
            selected = candidate(38.0, True)
            search = QualitySearchResult(
                spec=QualitySearchSpec(
                    QualityThresholds(83.0, 80.0, 0.950),
                    crf_max=38.0,
                ),
                points=[selected],
                evaluation_order=[38.0],
                selected=selected,
                monotonicity_violations=[],
                exhaustive_fallback=False,
            )
            output_info = video(root / "generic_no_roi.mp4", 300_000.0)
            toolchain = Toolchain(
                root / "ffmpeg",
                root / "ffprobe",
                root / "vmaf.json",
            )
            with patch(
                "hevc_lab.multi_encode.load_roi_settings",
            ) as load_roi, patch(
                "hevc_lab.multi_encode.run_scheme_quality_search",
                return_value=search,
            ) as search_mock, patch(
                "hevc_lab.multi_encode.evaluate_scheme_crf",
                return_value=selected,
            ) as evaluate, patch(
                "hevc_lab.multi_encode._publish_candidate",
                return_value=False,
            ), patch(
                "hevc_lab.multi_encode.probe_video",
                return_value=output_info,
            ):
                result = _composite_strategy(
                    toolchain,
                    input_source,
                    short_ref,
                    full_ref,
                    ROI_CONFIG,
                    root,
                    historical_strategy("general"),
                    default_bitrate_bps=500_000.0,
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["strategy_id"], "generic_no_roi")
            self.assertEqual(result["source_mode"], "aggressive")
            self.assertEqual(result["effective_preset"], "medium")
            self.assertFalse(result["region_processing_enabled"])
            self.assertEqual(result["saving_vs_general_no_roi_pct"], 0.0)
            self.assertIsNone(result["roi"])
            self.assertIsNone(result["denoise"])
            load_roi.assert_not_called()
            self.assertEqual(search_mock.call_args.kwargs["crf_max"], 38.0)
            self.assertIsNone(search_mock.call_args.kwargs["roi_settings"])
            self.assertIsNone(search_mock.call_args.kwargs["denoise_settings"])
            self.assertEqual(search_mock.call_args.kwargs["conditions"].preset, "medium")
            self.assertIsNone(evaluate.call_args.kwargs["roi_settings"])
            self.assertIsNone(evaluate.call_args.kwargs["denoise_settings"])

    def test_budget_neutral_roi_fails_when_bitrate_exceeds_general_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_source = video(root / "input.mp4")
            short_ref = reference(root / "short.mkv", 12.0)
            full_ref = reference(root / "full.mkv", 60.0)
            selected = candidate(36.0, True, bitrate=490_000.0)
            search = QualitySearchResult(
                spec=QualitySearchSpec(
                    QualityThresholds(83.0, 80.0, 0.950),
                    crf_max=38.0,
                ),
                points=[selected],
                evaluation_order=[36.0],
                selected=selected,
                monotonicity_violations=[],
                exhaustive_fallback=False,
            )
            over_budget = candidate(36.0, True, bitrate=510_000.0)
            toolchain = Toolchain(
                root / "ffmpeg",
                root / "ffprobe",
                root / "vmaf.json",
            )
            budget_reference = {
                "status": "completed",
                "average_video_packet_bitrate_bps": 500_000.0,
                "selected_candidate": candidate(36.5, True, bitrate=500_000.0).to_dict(),
            }
            with patch(
                "hevc_lab.multi_encode.run_scheme_quality_search",
                return_value=search,
            ), patch(
                "hevc_lab.multi_encode.evaluate_scheme_crf",
                return_value=over_budget,
            ), patch(
                "hevc_lab.multi_encode.evaluate_important_regions",
            ) as regions, patch("hevc_lab.multi_encode._publish_candidate") as publish:
                result = _composite_strategy(
                    toolchain,
                    input_source,
                    short_ref,
                    full_ref,
                    ROI_CONFIG,
                    root,
                    historical_strategy("roi"),
                    default_bitrate_bps=600_000.0,
                    budget_reference=budget_reference,
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["selection_reason"], "roi_budget_exceeded")
            self.assertFalse(result["budget_neutral_pass"])
            self.assertLess(result["saving_vs_general_no_roi_pct"], 0)
            regions.assert_not_called()
            publish.assert_not_called()

    def test_budget_neutral_roi_fails_when_important_region_quality_drops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_source = video(root / "input.mp4")
            short_ref = reference(root / "short.mkv", 12.0)
            full_ref = reference(root / "full.mkv", 60.0)
            selected = candidate(36.0, True, bitrate=490_000.0)
            search = QualitySearchResult(
                spec=QualitySearchSpec(QualityThresholds(83.0, 80.0, 0.950)),
                points=[selected],
                evaluation_order=[36.0],
                selected=selected,
                monotonicity_violations=[],
                exhaustive_fallback=False,
            )
            toolchain = Toolchain(root / "ffmpeg", root / "ffprobe", root / "vmaf.json")
            budget_reference = {
                "status": "completed",
                "average_video_packet_bitrate_bps": 500_000.0,
                "selected_candidate": candidate(36.5, True, bitrate=500_000.0).to_dict(),
            }
            with patch(
                "hevc_lab.multi_encode.run_scheme_quality_search",
                return_value=search,
            ), patch(
                "hevc_lab.multi_encode.evaluate_scheme_crf",
                return_value=selected,
            ), patch(
                "hevc_lab.multi_encode.evaluate_important_regions",
                return_value=region_quality(improved=False),
            ), patch("hevc_lab.multi_encode._publish_candidate") as publish:
                result = _composite_strategy(
                    toolchain,
                    input_source,
                    short_ref,
                    full_ref,
                    ROI_CONFIG,
                    root,
                    historical_strategy("roi"),
                    default_bitrate_bps=600_000.0,
                    budget_reference=budget_reference,
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["selection_reason"], "roi_region_quality_decreased")
            self.assertTrue(result["budget_neutral_pass"])
            self.assertFalse(result["roi_quality_preserved"])
            self.assertFalse(result["roi_quality_improved"])
            publish.assert_not_called()

    def test_budget_neutral_roi_passes_when_budget_and_region_quality_improve(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_source = video(root / "input.mp4")
            short_ref = reference(root / "short.mkv", 12.0)
            full_ref = reference(root / "full.mkv", 60.0)
            selected = candidate(36.0, True, bitrate=480_000.0)
            search = QualitySearchResult(
                spec=QualitySearchSpec(QualityThresholds(83.0, 80.0, 0.950)),
                points=[selected],
                evaluation_order=[36.0],
                selected=selected,
                monotonicity_violations=[],
                exhaustive_fallback=False,
            )
            output_info = video(root / "budget_neutral_roi.mp4", 480_000.0)
            toolchain = Toolchain(root / "ffmpeg", root / "ffprobe", root / "vmaf.json")
            budget_reference = {
                "status": "completed",
                "average_video_packet_bitrate_bps": 500_000.0,
                "selected_candidate": candidate(36.5, True, bitrate=500_000.0).to_dict(),
            }
            with patch(
                "hevc_lab.multi_encode.run_scheme_quality_search",
                return_value=search,
            ), patch(
                "hevc_lab.multi_encode.evaluate_scheme_crf",
                return_value=selected,
            ), patch(
                "hevc_lab.multi_encode.evaluate_important_regions",
                return_value=region_quality(improved=True),
            ), patch(
                "hevc_lab.multi_encode._publish_candidate",
                return_value=False,
            ), patch(
                "hevc_lab.multi_encode.probe_video",
                return_value=output_info,
            ):
                result = _composite_strategy(
                    toolchain,
                    input_source,
                    short_ref,
                    full_ref,
                    ROI_CONFIG,
                    root,
                    historical_strategy("roi"),
                    default_bitrate_bps=600_000.0,
                    budget_reference=budget_reference,
                )
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["budget_neutral_pass"])
            self.assertTrue(result["roi_quality_preserved"])
            self.assertTrue(result["roi_quality_improved"])
            self.assertAlmostEqual(result["saving_vs_general_no_roi_pct"], 4.0)
            self.assertEqual(
                result["roi_region_quality"][0]["quality_improved_vs_general"],
                True,
            )

    def test_v1_6_fixed_hevc_strategy_uses_screenshot_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_source = video(root / "input.mp4")
            full_ref = reference(root / "full.mkv", 60.0)
            fixed = candidate(36.0, True, bitrate=300_000.0)
            output_info = video(root / "hevc_fixed.mp4", 300_000.0)
            toolchain = Toolchain(root / "ffmpeg", root / "ffprobe", root / "vmaf.json")
            stages = []
            with patch(
                "hevc_lab.multi_encode.evaluate_scheme_crf",
                return_value=fixed,
            ) as evaluate, patch(
                "hevc_lab.multi_encode._publish_candidate",
                return_value=False,
            ), patch(
                "hevc_lab.multi_encode.probe_video",
                return_value=output_info,
            ):
                result = _fixed_hevc_strategy(
                    toolchain=toolchain,
                    input_source=input_source,
                    full_reference=full_ref,
                    output_dir=root,
                    default_bitrate_bps=500_000.0,
                    progress_callback=stages.append,
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["strategy_id"], "hevc_fixed")
            self.assertEqual(result["selected_crf"], 36.0)
            self.assertEqual(result["effective_preset"], "medium")
            self.assertFalse(result["roi_enabled"])
            self.assertFalse(result["denoise_enabled"])
            self.assertEqual(stages, ["encoding_hevc_fixed"])
            self.assertEqual(evaluate.call_args.kwargs["crf"], 36.0)
            self.assertIsNone(evaluate.call_args.kwargs["roi_settings"])
            self.assertIsNone(evaluate.call_args.kwargs["denoise_settings"])
            self.assertEqual(evaluate.call_args.kwargs["conditions"].preset, "medium")
            self.assertIn("keyint=200", evaluate.call_args.kwargs["scheme"].x265_params(20.0))
            self.assertIn("ref=6", evaluate.call_args.kwargs["scheme"].x265_params(20.0))
            self.assertIn("bframes=8", evaluate.call_args.kwargs["scheme"].x265_params(20.0))
            self.assertIn("rc-lookahead=90", evaluate.call_args.kwargs["scheme"].x265_params(20.0))

    def test_run_multi_encode_outputs_h264_native_and_fixed_hevc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.mp4"
            input_path.write_bytes(b"input")
            toolchain = Toolchain(root / "ffmpeg", root / "ffprobe", root / "vmaf.json")
            input_info = video(input_path)
            full_ref = reference(root / "full.mkv", 60.0)

            def fake_writer(output_dir, payload):
                return payload

            with patch(
                "hevc_lab.multi_encode.probe_video",
                return_value=input_info,
            ), patch(
                "hevc_lab.multi_encode.prepare_reference",
                return_value=full_ref,
            ), patch(
                "hevc_lab.multi_encode._h264_native_strategy",
                return_value={
                    "strategy_id": "default_h264",
                    "title": "H.264 原生编码",
                    "mode": "h264_native",
                    "status": "completed",
                    "average_video_packet_bitrate_bps": 500_000.0,
                    "saving_vs_default_pct": None,
                },
            ), patch(
                "hevc_lab.multi_encode._fixed_hevc_strategy",
                return_value={
                    "strategy_id": "hevc_fixed",
                    "title": "H.265 固定参数方案",
                    "mode": "hevc_fixed",
                    "status": "completed",
                    "average_video_packet_bitrate_bps": 300_000.0,
                    "saving_vs_default_pct": 40.0,
                },
            ), patch(
                "hevc_lab.multi_encode.write_multi_encode_reports",
                side_effect=fake_writer,
            ):
                payload = run_multi_encode(
                    toolchain,
                    input_path,
                    ROI_CONFIG,
                    root / "out",
                )
            self.assertEqual(
                [item["strategy_id"] for item in payload["strategies"]],
                [
                    "default_h264",
                    "hevc_fixed",
                ],
            )
            self.assertEqual(payload["pipeline_version"], "v1.6.0")
            self.assertEqual(payload["comparison_policy"]["default_strategy_id"], "default_h264")
            self.assertFalse(payload["comparison_policy"]["roi_enabled"])


class MultiEncodeReportTests(unittest.TestCase):
    def test_final_report_has_two_rows_and_preserves_negative_saving(self):
        strategies = [
            {
                "title": "H.264 原生编码",
                "strategy_id": "default_h264",
                "status": "completed",
                "resolution": "1920x1080",
                "average_video_packet_bitrate_bps": 500_000.0,
                "saving_vs_default_pct": None,
                "target_saving_min_pct": None,
                "target_saving_max_pct": None,
                "target_saving_met": None,
                "saving_target_status": "not_applicable",
                "selection_reason": "h264_native_reference",
            },
            {
                "title": "H.265 固定参数方案",
                "strategy_id": "hevc_fixed",
                "status": "completed",
                "resolution": "1920x1080",
                "average_video_packet_bitrate_bps": 600_000.0,
                "selected_crf": 36.0,
                "vmaf_mean": 85.0,
                "vmaf_p5": 82.0,
                "ssim": 0.960,
                "encode_speed_x": 0.65,
                "saving_vs_default_pct": -20.0,
                "saving_vs_general_no_roi_pct": None,
                "budget_neutral_pass": None,
                "roi_quality_preserved": None,
                "roi_quality_improved": None,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_multi_encode_reports(root, {"strategies": strategies})
            with (root / "final_metrics.csv").open(
                newline="",
                encoding="utf-8-sig",
            ) as stream:
                rows = list(csv.DictReader(stream))
            manifest = json.loads(
                (root / "research_manifest.json").read_text(encoding="utf-8")
            )
            summary = (root / "final_summary.md").read_text(encoding="utf-8")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["saving_vs_default_pct"], "-20.00")
        self.assertEqual(rows[1]["saving_vs_general_no_roi_pct"], "")
        self.assertEqual(rows[1]["selected_crf"], "36.0")
        self.assertEqual(rows[1]["vmaf_mean"], "85.000")
        self.assertEqual(rows[1]["ssim"], "0.960000")
        self.assertEqual(len(manifest["strategies"]), 2)
        self.assertIn("V1.6 H.264 原生编码与 H.265 固定参数方案", summary)
        self.assertIn("-20.00%", summary)
        self.assertIn("CRF 36.0", summary)
        self.assertIn("无 ROI、无降噪", summary)
        self.assertNotIn("推荐方案", summary)
        self.assertIn("不输出部署结论", summary)


if __name__ == "__main__":
    unittest.main()
