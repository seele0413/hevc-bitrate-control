import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hevc_lab.core.configs import denoise_policy_for_mode
from hevc_lab.core.models import (
    CandidateResult,
    DenoiseSettings,
    ReferenceArtifact,
    Toolchain,
    VideoInfo,
)
from hevc_lab.core.roi import load_roi_settings
from hevc_lab.core.search import (
    QualitySearchResult,
    QualitySearchSpec,
    QualityThresholds,
)
from hevc_lab.encoders.x265 import (
    combined_roi_denoise_filter,
    encode_default_x265,
)
from hevc_lab.multi_encode import (
    _composite_strategy,
    bitrate_saving_vs_default_pct,
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


def candidate(crf, passed, path="candidate.mp4", cache_hit=False):
    return CandidateResult(
        name="optimized",
        title="综合策略",
        description="test",
        output_path=path,
        x265_params="test=1",
        crf=crf,
        preset="medium",
        bitrate_bps=400_000.0,
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


class MultiEncodeDecisionTests(unittest.TestCase):
    def test_saving_keeps_positive_zero_and_negative_values(self):
        self.assertAlmostEqual(bitrate_saving_vs_default_pct(100, 80), 20.0)
        self.assertAlmostEqual(bitrate_saving_vs_default_pct(100, 100), 0.0)
        self.assertAlmostEqual(bitrate_saving_vs_default_pct(100, 120), -20.0)
        with self.assertRaises(ValueError):
            bitrate_saving_vs_default_pct(0, 80)

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
            output_info = video(root / "composite_balanced.mp4", 350_000.0)
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
                    "balanced",
                    progress_callback=stages.append,
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["selected_crf"], 25.5)
            self.assertEqual(stages, ["searching_balanced", "validating_balanced"])
            self.assertEqual(
                [call.kwargs["crf"] for call in evaluate.call_args_list],
                [26.0, 25.5],
            )


class MultiEncodeReportTests(unittest.TestCase):
    def test_final_report_has_four_rows_and_preserves_negative_saving(self):
        strategies = [
            {
                "title": "x265原生默认",
                "status": "completed",
                "resolution": "1920x1080",
                "average_video_packet_bitrate_bps": 500_000.0,
                "saving_vs_default_pct": None,
            },
            {
                "title": "保守模式综合策略",
                "status": "completed",
                "resolution": "1920x1080",
                "average_video_packet_bitrate_bps": 450_000.0,
                "saving_vs_default_pct": 10.0,
            },
            {
                "title": "综合模式综合策略",
                "status": "completed",
                "resolution": "1920x1080",
                "average_video_packet_bitrate_bps": 500_000.0,
                "saving_vs_default_pct": 0.0,
            },
            {
                "title": "激进模式综合策略",
                "status": "completed",
                "resolution": "1920x1080",
                "average_video_packet_bitrate_bps": 550_000.0,
                "saving_vs_default_pct": -10.0,
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
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1]["saving_vs_default_pct"], "-10.00")
        self.assertEqual(len(manifest["strategies"]), 4)
        self.assertIn("-10.00%", summary)
        self.assertNotIn("推荐方案", summary)


if __name__ == "__main__":
    unittest.main()
