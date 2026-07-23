import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hevc_lab.comparison import (
    build_comparison_request,
    comparison_cache_key,
    run_comparison,
)
from hevc_lab.core.models import Toolchain


def fake_payload():
    return {
        "schema_version": 2,
        "study": "test",
        "mode": {"name": "balanced", "title": "综合模式"},
        "searches": {
            "baseline": {"points": []},
            "optimized": {"points": []},
        },
        "match": {"status": "insufficient_evidence", "pair": None},
        "continuity_validation": {"checked": False, "output_path": ""},
        "conclusions": {
            "algorithm": {"title": "算法可行性", "passed": False, "reason": "test"},
            "software_continuity": {"title": "软件画面连续性", "passed": False, "reason": "test"},
            "deployment": {"title": "部署可行性初筛", "passed": False, "reason": "test"},
        },
    }


class ComparisonCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.input_path = self.root / "input.mp4"
        self.input_path.write_bytes(b"input-content")
        self.model_path = self.root / "vmaf.json"
        self.model_path.write_bytes(b"model-content")
        self.toolchain = Toolchain(
            ffmpeg=self.root / "ffmpeg.exe",
            ffprobe=self.root / "ffprobe.exe",
            vmaf_model=self.model_path,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def request(self, **overrides):
        values = {
            "toolchain": self.toolchain,
            "input_path": self.input_path,
            "mode": "balanced",
            "preset": None,
            "target_vmaf": None,
            "target_vmaf_p5": None,
            "target_ssim": None,
            "min_speed": None,
            "min_saving": None,
            "min_algorithm_saving": None,
            "min_source_saving": None,
            "max_vmaf_delta": 1.0,
            "start_seconds": 0.0,
            "duration_seconds": 12.0,
        }
        values.update(overrides)
        return build_comparison_request(**values)

    def test_request_key_is_stable_and_covers_effective_settings(self):
        first = self.request(min_saving=4.0, min_source_saving=6.0)
        second = self.request(min_saving=4.0, min_source_saving=6.0)
        changed = self.request(duration_seconds=13.0)
        self.assertEqual(first["min_algorithm_saving_pct"], 4.0)
        self.assertEqual(first["min_source_saving_pct"], 6.0)
        self.assertEqual(comparison_cache_key(first), comparison_cache_key(second))
        self.assertNotEqual(comparison_cache_key(first), comparison_cache_key(changed))

    def test_completed_identical_request_hits_experiment_cache(self):
        output = self.root / "results"
        with patch(
            "hevc_lab.comparison.run_pair_quality_search",
            return_value=fake_payload(),
        ) as runner:
            first = run_comparison(
                self.toolchain,
                self.input_path,
                output,
                duration_seconds=12.0,
            )
            second = run_comparison(
                self.toolchain,
                self.input_path,
                output,
                duration_seconds=12.0,
            )
        self.assertEqual(runner.call_count, 1)
        self.assertFalse(first["comparison_cache"]["experiment_cache_hit"])
        self.assertTrue(second["comparison_cache"]["experiment_cache_hit"])
        state = json.loads((output / "comparison_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["attempt"], 1)

    def test_failed_same_request_resumes_on_next_attempt(self):
        output = self.root / "resume"

        def fail(**kwargs):
            kwargs["progress_callback"]("search_baseline")
            raise RuntimeError("simulated interruption")

        with patch(
            "hevc_lab.comparison.run_pair_quality_search",
            side_effect=fail,
        ):
            with self.assertRaises(RuntimeError):
                run_comparison(
                    self.toolchain,
                    self.input_path,
                    output,
                    duration_seconds=12.0,
                )
        failed = json.loads((output / "comparison_state.json").read_text(encoding="utf-8"))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["stage"], "search_baseline")

        with patch(
            "hevc_lab.comparison.run_pair_quality_search",
            return_value=fake_payload(),
        ):
            resumed = run_comparison(
                self.toolchain,
                self.input_path,
                output,
                duration_seconds=12.0,
            )
        self.assertTrue(resumed["comparison_cache"]["resumed"])
        self.assertEqual(resumed["comparison_cache"]["attempt"], 2)

    def test_changed_request_starts_new_experiment_identity(self):
        output = self.root / "changed"
        with patch(
            "hevc_lab.comparison.run_pair_quality_search",
            return_value=fake_payload(),
        ) as runner:
            run_comparison(
                self.toolchain,
                self.input_path,
                output,
                duration_seconds=12.0,
            )
            changed = run_comparison(
                self.toolchain,
                self.input_path,
                output,
                duration_seconds=13.0,
            )
        self.assertEqual(runner.call_count, 2)
        self.assertFalse(changed["comparison_cache"]["resumed"])
        self.assertEqual(changed["comparison_cache"]["attempt"], 1)


if __name__ == "__main__":
    unittest.main()
