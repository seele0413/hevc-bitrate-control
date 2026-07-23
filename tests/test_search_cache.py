import unittest
from pathlib import Path

from hevc_lab.core.configs import (
    aq_profiles_for_mode,
    default_aq_profile,
    denoise_policy_for_mode,
    v1_comparison_plan,
)
from hevc_lab.core.models import DenoiseSettings
from hevc_lab.quality_search import build_candidate_cache_key
from hevc_lab.core.roi import load_roi_settings


class SearchCacheKeyTests(unittest.TestCase):
    def test_key_is_stable_and_covers_crf_and_parameters(self):
        plan = v1_comparison_plan("balanced")
        values = {
            "input_sha256": "input-hash",
            "reference_cache_key": "reference-hash",
            "config": plan.optimized,
            "conditions": plan.conditions,
            "fps": 25.0,
            "crf": 28.0,
            "vmaf_model_sha256": "model-hash",
        }
        first = build_candidate_cache_key(**values)
        self.assertEqual(first, build_candidate_cache_key(**values))
        self.assertNotEqual(
            first,
            build_candidate_cache_key(**{**values, "crf": 28.5}),
        )
        self.assertNotEqual(
            first,
            build_candidate_cache_key(**values, min_speed_x=None),
        )
        self.assertNotEqual(
            first,
            build_candidate_cache_key(
                **{
                    **values,
                    "config": v1_comparison_plan("aggressive").optimized,
                }
            ),
        )
        self.assertNotEqual(
            first,
            build_candidate_cache_key(
                **{
                    **values,
                    "conditions": v1_comparison_plan("aggressive").conditions,
                }
            ),
        )
        roi_balanced = load_roi_settings(
            Path(__file__).resolve().parents[1] / "configs" / "camera-entrance-roi.json",
            "balanced",
        )
        roi_aggressive = load_roi_settings(
            Path(__file__).resolve().parents[1] / "configs" / "camera-entrance-roi.json",
            "aggressive",
        )
        self.assertNotEqual(
            first,
            build_candidate_cache_key(**values, roi_settings=roi_balanced),
        )
        self.assertNotEqual(
            build_candidate_cache_key(**values, roi_settings=roi_balanced),
            build_candidate_cache_key(**values, roi_settings=roi_aggressive),
        )
        denoise = DenoiseSettings(
            roi_balanced,
            denoise_policy_for_mode("balanced"),
        )
        self.assertNotEqual(
            first,
            build_candidate_cache_key(**values, denoise_settings=denoise),
        )
        self.assertNotEqual(
            build_candidate_cache_key(
                **values,
                adaptive_quantization=default_aq_profile(),
            ),
            build_candidate_cache_key(
                **values,
                adaptive_quantization=aq_profiles_for_mode("balanced")[1],
            ),
        )


if __name__ == "__main__":
    unittest.main()
