import unittest

from hevc_lab.cli import build_parser


class CliModeTests(unittest.TestCase):
    def test_multi_encode_requires_input_roi_and_output(self):
        args = build_parser().parse_args(
            [
                "multi-encode",
                "--input",
                "input.mp4",
                "--roi-config",
                "camera.json",
                "--output",
                "results",
            ]
        )
        self.assertEqual(args.command, "multi-encode")
        self.assertEqual(str(args.roi_config), "camera.json")

    def test_web_defaults_to_localhost_and_port_8000(self):
        args = build_parser().parse_args(["web"])
        self.assertEqual(args.command, "web")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)

    def test_experiment_defaults_to_balanced_mode_settings(self):
        args = build_parser().parse_args(
            ["experiment", "--input", "input.mp4", "--output", "results"]
        )
        self.assertEqual(args.mode, "balanced")
        self.assertIsNone(args.crf)
        self.assertIsNone(args.target_vmaf)

    def test_experiment_accepts_mode_and_explicit_override(self):
        args = build_parser().parse_args(
            [
                "experiment",
                "--input",
                "input.mp4",
                "--output",
                "results",
                "--mode",
                "aggressive",
                "--crf",
                "23.5",
            ]
        )
        self.assertEqual(args.mode, "aggressive")
        self.assertEqual(args.crf, 23.5)

    def test_search_crf_accepts_one_scheme_and_uses_balanced_by_default(self):
        args = build_parser().parse_args(
            [
                "search-crf",
                "--input",
                "input.mp4",
                "--output",
                "results",
                "--scheme",
                "baseline",
            ]
        )
        self.assertEqual(args.mode, "balanced")
        self.assertEqual(args.scheme, "baseline")

    def test_pair_crf_uses_one_point_zero_delta_limit(self):
        args = build_parser().parse_args(
            ["pair-crf", "--input", "input.mp4", "--output", "results"]
        )
        self.assertEqual(args.mode, "balanced")
        self.assertEqual(args.max_vmaf_delta, 1.0)

    def test_compare_defaults_and_saving_overrides(self):
        args = build_parser().parse_args(
            [
                "compare",
                "--input",
                "input.mp4",
                "--output",
                "results",
                "--min-saving",
                "4",
                "--min-source-saving",
                "6",
            ]
        )
        self.assertEqual(args.mode, "balanced")
        self.assertEqual(args.max_vmaf_delta, 1.0)
        self.assertEqual(args.min_saving, 4.0)
        self.assertIsNone(args.min_algorithm_saving)
        self.assertEqual(args.min_source_saving, 6.0)

    def test_rate_control_uses_balanced_optimized_defaults(self):
        args = build_parser().parse_args(
            ["rate-control", "--input", "input.mp4", "--output", "results"]
        )
        self.assertEqual(args.mode, "balanced")
        self.assertEqual(args.scheme, "optimized")
        self.assertEqual(args.maximum_peak_ratio, 2.5)

    def test_aq_study_uses_balanced_optimized_defaults(self):
        args = build_parser().parse_args(
            ["aq-study", "--input", "input.mp4", "--output", "results"]
        )
        self.assertEqual(args.mode, "balanced")
        self.assertEqual(args.scheme, "optimized")
        self.assertEqual(args.max_vmaf_delta, 1.0)

    def test_preset_study_uses_aggressive_optimized_defaults(self):
        args = build_parser().parse_args(
            ["preset-study", "--input", "input.mp4", "--output", "results"]
        )
        self.assertEqual(args.mode, "aggressive")
        self.assertEqual(args.scheme, "optimized")
        self.assertEqual(args.max_vmaf_delta, 1.0)

    def test_roi_study_requires_config_and_uses_balanced_defaults(self):
        args = build_parser().parse_args(
            [
                "roi-study",
                "--input",
                "input.mp4",
                "--roi-config",
                "camera.json",
                "--output",
                "results",
            ]
        )
        self.assertEqual(args.mode, "balanced")
        self.assertEqual(args.scheme, "optimized")
        self.assertEqual(args.max_vmaf_delta, 1.0)
        self.assertEqual(str(args.roi_config), "camera.json")

    def test_denoise_study_requires_roi_config_and_uses_balanced_defaults(self):
        args = build_parser().parse_args(
            [
                "denoise-study",
                "--input",
                "input.mp4",
                "--roi-config",
                "camera.json",
                "--output",
                "results",
            ]
        )
        self.assertEqual(args.mode, "balanced")
        self.assertEqual(args.scheme, "optimized")
        self.assertEqual(args.max_vmaf_delta, 1.0)


if __name__ == "__main__":
    unittest.main()
