import unittest

from hevc_lab.core.matching import (
    boundary_quality_candidates,
    match_equal_quality_candidates,
)
from hevc_lab.core.models import CandidateResult
from hevc_lab.core.search import QualitySearchResult, QualitySearchSpec, QualityThresholds


def candidate(name, crf, vmaf, bitrate, passing=True):
    return CandidateResult(
        name=name,
        title=name,
        description="",
        output_path=f"{name}-{crf:.1f}.mp4",
        x265_params="",
        crf=crf,
        preset="medium",
        bitrate_bps=bitrate,
        file_size_bytes=1,
        vmaf_mean=vmaf,
        vmaf_p5=94 if passing else 90,
        ssim=0.995 if passing else 0.98,
        encode_seconds=1,
        encode_speed_x=2,
        quality_pass=passing,
        speed_pass=True,
        eligible=passing,
    )


def search_result(name, rows, selected_crf=None):
    points = [candidate(name, *row) for row in rows]
    selected = next(
        (point for point in points if point.crf == selected_crf),
        None,
    )
    return QualitySearchResult(
        spec=QualitySearchSpec(
            thresholds=QualityThresholds(95, 93, 0.99)
        ),
        points=points,
        evaluation_order=[point.crf for point in points],
        selected=selected,
        monotonicity_violations=[],
        exhaustive_fallback=False,
    )


class EqualQualityMatchingTests(unittest.TestCase):
    def test_boundary_pool_excludes_high_quality_anchor_points(self):
        search = search_result(
            "baseline",
            [
                (18.0, 99.0, 500_000, True),
                (28.5, 95.8, 220_000, True),
                (29.0, 95.1, 200_000, True),
                (29.5, 94.7, 180_000, False),
            ],
            selected_crf=29.0,
        )
        self.assertEqual(
            [item.crf for item in boundary_quality_candidates(search)],
            [29.0, 28.5],
        )

    def test_matches_closest_boundary_pair_and_calculates_saving(self):
        baseline = search_result(
            "baseline",
            [
                (18.0, 99.0, 500_000, True),
                (28.5, 95.8, 220_000, True),
                (29.0, 95.0, 200_000, True),
            ],
            selected_crf=29.0,
        )
        optimized = search_result(
            "optimized",
            [
                (18.0, 99.0, 480_000, True),
                (29.0, 96.0, 190_000, True),
                (29.5, 95.6, 176_000, True),
            ],
            selected_crf=29.5,
        )
        result = match_equal_quality_candidates(baseline, optimized)
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.pair.baseline.crf, 28.5)
        self.assertEqual(result.pair.optimized.crf, 29.5)
        self.assertAlmostEqual(result.pair.vmaf_delta, 0.2)
        self.assertAlmostEqual(result.pair.algorithm_saving_pct, 20.0)
        self.assertEqual(result.evaluated_pair_count, 4)

    def test_delta_exactly_one_is_allowed(self):
        baseline = search_result(
            "baseline", [(29.0, 95.0, 200_000, True)], selected_crf=29.0
        )
        optimized = search_result(
            "optimized", [(29.0, 96.0, 180_000, True)], selected_crf=29.0
        )
        result = match_equal_quality_candidates(baseline, optimized)
        self.assertEqual(result.status, "matched")
        self.assertAlmostEqual(result.pair.vmaf_delta, 1.0)

    def test_speed_ineligible_boundary_point_is_excluded(self):
        search = search_result(
            "baseline",
            [(28.5, 95.5, 220_000, True), (29.0, 95.0, 200_000, True)],
            selected_crf=29.0,
        )
        search.selected.eligible = False
        self.assertEqual(
            [item.crf for item in boundary_quality_candidates(search)],
            [28.5],
        )

    def test_returns_insufficient_evidence_when_delta_is_too_large(self):
        baseline = search_result(
            "baseline", [(29.0, 95.0, 200_000, True)], selected_crf=29.0
        )
        optimized = search_result(
            "optimized", [(29.0, 96.01, 180_000, True)], selected_crf=29.0
        )
        result = match_equal_quality_candidates(baseline, optimized)
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertIsNone(result.pair)
        self.assertIn("1.010", result.reason)

    def test_returns_insufficient_evidence_when_a_search_has_no_pass(self):
        baseline = search_result(
            "baseline", [(18.0, 94.0, 300_000, False)], selected_crf=None
        )
        optimized = search_result(
            "optimized", [(29.0, 95.0, 180_000, True)], selected_crf=29.0
        )
        result = match_equal_quality_candidates(baseline, optimized)
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertIn("工程基线", result.reason)


if __name__ == "__main__":
    unittest.main()
