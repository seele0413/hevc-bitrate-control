import unittest


class ModuleBoundaryTests(unittest.TestCase):
    def test_new_layer_packages_are_importable(self):
        from hevc_lab import adapters, core, encoders, metrics, reports, web

        self.assertTrue(callable(core.calculate_saving))
        self.assertTrue(callable(core.adaptive_quality_search))
        self.assertTrue(callable(core.match_equal_quality_candidates))
        self.assertTrue(callable(core.derive_vbv_settings))
        self.assertTrue(callable(core.select_best_aq_trial))
        self.assertTrue(callable(encoders.encode_candidate))
        self.assertTrue(callable(metrics.compute_quality))
        self.assertTrue(callable(adapters.probe_video))
        self.assertTrue(callable(reports.write_reports))
        self.assertTrue(callable(reports.write_aq_study_reports))
        self.assertIsNotNone(web.__doc__)

    def test_legacy_imports_resolve_to_reorganized_implementations(self):
        from hevc_lab.adapters.video_input import parse_fraction as new_parse_fraction
        from hevc_lab.core.models import CandidateResult as NewCandidateResult
        from hevc_lab.models import CandidateResult as LegacyCandidateResult
        from hevc_lab.probe import parse_fraction as legacy_parse_fraction
        from hevc_lab.report import write_reports as legacy_write_reports
        from hevc_lab.reports import write_reports as new_write_reports

        self.assertIs(LegacyCandidateResult, NewCandidateResult)
        self.assertIs(legacy_parse_fraction, new_parse_fraction)
        self.assertIs(legacy_write_reports, new_write_reports)


if __name__ == "__main__":
    unittest.main()
