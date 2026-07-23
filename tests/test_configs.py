import unittest

from hevc_lab.core.configs import (
    available_modes,
    get_mode_policy,
    interframe_configs,
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
        self.assertEqual(optimized_params["keyint"], "100")
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
            ("conservative", "balanced", "aggressive"),
        )
        policies = [get_mode_policy(name) for name in available_modes()]
        self.assertEqual([item.default_crf for item in policies], [20.0, 22.0, 24.0])
        self.assertEqual([item.target_vmaf for item in policies], [95.0, 90.0, 83.0])
        self.assertEqual([item.target_vmaf_p5 for item in policies], [93.0, 88.0, 80.0])
        self.assertEqual([item.target_ssim for item in policies], [0.990, 0.980, 0.950])
        self.assertEqual([item.preset for item in policies], ["medium", "medium", "slow"])
        self.assertEqual([item.min_speed_x for item in policies], [0.97, 0.97, None])
        self.assertEqual(
            [item.to_dict()["speed_gate_enabled"] for item in policies],
            [True, True, False],
        )
        self.assertEqual([item.vbv_peak_ratio for item in policies], [2.0, 1.5, 1.25])
        self.assertEqual([item.vbv_buffer_seconds for item in policies], [4.0, 3.0, 2.0])
        plans = [v1_comparison_plan(name) for name in available_modes()]
        self.assertEqual(
            [item.optimized.gop_seconds for item in plans],
            [2, 4, 10],
        )
        self.assertEqual(
            [item.optimized.lookahead for item in plans],
            [30, 60, 90],
        )
        aggressive_params = plans[-1].optimized.x265_params(20)
        self.assertIn("keyint=200", aggressive_params)
        self.assertIn("min-keyint=40", aggressive_params)
        self.assertIn("b-pyramid=1", aggressive_params)

    def test_modes_share_baseline_params_but_use_their_declared_preset(self):
        baseline_params = {
            v1_comparison_plan(name).baseline.x265_params(25)
            for name in available_modes()
        }
        self.assertEqual(len(baseline_params), 1)
        self.assertEqual(
            [v1_comparison_plan(name).conditions.preset for name in available_modes()],
            ["medium", "medium", "slow"],
        )

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            get_mode_policy("unknown")
        with self.assertRaises(ValueError):
            v1_comparison_plan("unknown")


if __name__ == "__main__":
    unittest.main()
