import unittest

from hevc_lab.core.configs import (
    available_modes,
    get_mode_policy,
    interframe_configs,
    multi_encode_modes,
    multi_encode_strategies,
    v1_comparison_plan,
)


class InterConfigTests(unittest.TestCase):
    def test_config_names_are_unique_and_include_baseline(self):
        configs = interframe_configs()
        names = [config.name for config in configs]
        self.assertEqual(names, ["baseline", "optimized"])
        self.assertEqual(len(names), len(set(names)))

    def test_x265_params_follow_source_fps(self):
        baseline = interframe_configs()[0]
        params = baseline.x265_params(25.0)
        self.assertIn("keyint=50", params)
        self.assertIn("min-keyint=25", params)
        self.assertIn("b-adapt=2", params)

    def test_v1_models_match_design_and_only_change_interframe_parameters(self):
        plan = v1_comparison_plan()
        self.assertEqual(
            plan.conditions.to_dict(),
            {
                "encoder": "libx265",
                "preset": "medium",
                "profile": "main",
                "pixel_format": "yuv420p",
                "preset_source": "mode",
                "mode_default_preset": "medium",
            },
        )
        baseline = plan.baseline.x265_params(25).split(":")
        optimized = plan.optimized.x265_params(25).split(":")
        baseline_params = dict(item.split("=", 1) for item in baseline)
        optimized_params = dict(item.split("=", 1) for item in optimized)
        self.assertEqual(baseline_params["keyint"], "50")
        self.assertEqual(optimized_params["keyint"], "250")
        self.assertEqual(baseline_params["min-keyint"], "25")
        self.assertEqual(optimized_params["min-keyint"], "50")
        changed = {
            key
            for key in baseline_params
            if baseline_params[key] != optimized_params[key]
        }
        self.assertEqual(changed, {"ref", "bframes", "rc-lookahead", "keyint", "min-keyint"})

    def test_modes_follow_declared_pareto_order(self):
        self.assertEqual(
            available_modes(),
            (
                "conservative",
                "balanced",
                "aggressive",
                "aggressive_plus",
                "aggressive_plus_plus",
                "aggressive_plus_plus_plus",
            ),
        )
        policies = [get_mode_policy(name) for name in available_modes()]
        self.assertEqual(
            [item.default_crf for item in policies],
            [20.0, 22.0, 24.0, 24.0, 24.0, 24.0],
        )
        self.assertEqual(
            [item.target_vmaf for item in policies],
            [90.0, 90.0, 83.0, 83.0, 83.0, 83.0],
        )
        self.assertEqual(
            [item.target_vmaf_p5 for item in policies],
            [88.0, 88.0, 80.0, 80.0, 80.0, 80.0],
        )
        self.assertEqual(
            [item.target_ssim for item in policies],
            [0.980, 0.980, 0.950, 0.950, 0.950, 0.950],
        )
        self.assertEqual(
            [item.preset for item in policies],
            ["medium", "medium", "slow", "slow", "slow", "slow"],
        )
        self.assertEqual(
            [item.min_speed_x for item in policies],
            [0.97, 0.97, None, None, None, None],
        )
        self.assertEqual(
            [
                (item.target_saving_min_pct, item.target_saving_max_pct)
                for item in policies
            ],
            [
                (10.0, 15.0),
                (20.0, 30.0),
                (None, None),
                (None, None),
                (None, None),
                (None, None),
            ],
        )
        self.assertEqual(
            [item.to_dict()["speed_gate_enabled"] for item in policies],
            [True, True, False, False, False, False],
        )
        self.assertEqual(
            [item.vbv_peak_ratio for item in policies],
            [2.0, 1.5, 1.25, 1.25, 1.25, 1.25],
        )
        self.assertEqual(
            [item.vbv_buffer_seconds for item in policies],
            [4.0, 3.0, 2.0, 2.0, 2.0, 2.0],
        )
        self.assertEqual(
            [item.crf_search_max for item in policies],
            [38.0, 38.0, 38.0, 42.0, 45.0, 48.0],
        )
        plans = [v1_comparison_plan(name) for name in available_modes()]
        self.assertEqual(
            [item.optimized.gop_seconds for item in plans],
            [8, 10, 10, 12, 15, 20],
        )
        self.assertEqual(
            [item.optimized.lookahead for item in plans],
            [45, 60, 45, 100, 120, 150],
        )
        conservative_params = plans[0].optimized.x265_params(20)
        balanced_params = plans[1].optimized.x265_params(20)
        aggressive_params = plans[2].optimized.x265_params(20)
        aggressive_plus_params = plans[3].optimized.x265_params(20)
        aggressive_plus_plus_params = plans[4].optimized.x265_params(20)
        aggressive_plus_plus_plus_params = plans[5].optimized.x265_params(20)
        self.assertIn("keyint=160", conservative_params)
        self.assertIn("min-keyint=40", conservative_params)
        self.assertIn("keyint=200", balanced_params)
        self.assertIn("min-keyint=40", balanced_params)
        self.assertIn("keyint=200", aggressive_params)
        self.assertIn("min-keyint=40", aggressive_params)
        self.assertIn("b-pyramid=1", aggressive_params)
        self.assertIn("keyint=240", aggressive_plus_params)
        self.assertIn("min-keyint=40", aggressive_plus_params)
        self.assertIn("keyint=300", aggressive_plus_plus_params)
        self.assertIn("min-keyint=40", aggressive_plus_plus_params)
        self.assertIn("keyint=400", aggressive_plus_plus_plus_params)
        self.assertIn("min-keyint=40", aggressive_plus_plus_plus_params)

    def test_v1_6_multi_encode_outputs_h264_native_and_fixed_hevc_plan(self):
        self.assertEqual(
            multi_encode_modes(),
            (
                "h264_native",
                "hevc_fixed",
            ),
        )
        strategies = multi_encode_strategies()
        self.assertEqual(
            [item.strategy_id for item in strategies],
            [
                "hevc_fixed",
            ],
        )
        self.assertEqual(
            [item.source_mode for item in strategies],
            ["aggressive"],
        )
        self.assertEqual(
            [item.effective_preset for item in strategies],
            ["medium"],
        )
        self.assertEqual(
            [item.crf_search_max for item in strategies],
            [36.0],
        )
        self.assertEqual(
            [
                (
                    item.target_vmaf,
                    item.target_vmaf_p5,
                    item.target_ssim,
                    item.crf_search_min,
                    item.crf_search_max,
                )
                for item in strategies
            ],
            [(83.0, 80.0, 0.950, 36.0, 36.0)],
        )
        self.assertEqual(
            [(item.roi_enabled, item.denoise_enabled) for item in strategies],
            [(False, False)],
        )
        self.assertEqual(
            [item.budget_neutral_required for item in strategies],
            [False],
        )
        self.assertEqual(
            [item.roi_quality_required for item in strategies],
            [False],
        )
        self.assertEqual(
            [item.budget_reference for item in strategies],
            [None],
        )

    def test_modes_share_baseline_params_but_use_their_declared_preset(self):
        baseline_params = {
            v1_comparison_plan(name).baseline.x265_params(25)
            for name in available_modes()
        }
        self.assertEqual(len(baseline_params), 1)
        self.assertEqual(
            [v1_comparison_plan(name).conditions.preset for name in available_modes()],
            ["medium", "medium", "slow", "slow", "slow", "slow"],
        )

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            get_mode_policy("unknown")
        with self.assertRaises(ValueError):
            v1_comparison_plan("unknown")


if __name__ == "__main__":
    unittest.main()
