import unittest

from hevc_lab.core.matching import EqualQualityMatchResult, EqualQualityPair
from hevc_lab.core.models import CandidateResult
from hevc_lab.core.preset import decide_preset_study


def candidate(name, bitrate):
    return CandidateResult(
        name=name,
        title=name,
        description="",
        output_path=f"{name}.mp4",
        x265_params="",
        crf=25.0,
        preset=name,
        bitrate_bps=bitrate,
        file_size_bytes=1,
        vmaf_mean=95.0,
        vmaf_p5=93.0,
        ssim=0.99,
        encode_seconds=1.0,
        encode_speed_x=0.5,
        quality_pass=True,
        speed_pass=True,
        eligible=True,
    )


def match(saving):
    medium = candidate("medium", 1_000_000)
    slow = candidate("slow", 1_000_000 * (1 - saving / 100))
    pair = EqualQualityPair(medium, slow, 0.1, 0.1, 0.0001, saving)
    return EqualQualityMatchResult(
        "matched", 1.0, pair, "matched", (25.0,), (25.0,), 1, 1
    )


class PresetStudyDecisionTests(unittest.TestCase):
    def test_positive_slow_saving_is_confirmed(self):
        decision = decide_preset_study(match(4.0))
        self.assertTrue(decision.benefit_confirmed)
        self.assertEqual(decision.status, "slow_benefit_confirmed")

    def test_non_positive_saving_is_not_confirmed(self):
        decision = decide_preset_study(match(-1.0))
        self.assertFalse(decision.benefit_confirmed)
        self.assertEqual(decision.status, "slow_benefit_not_confirmed")

    def test_missing_pair_is_insufficient_evidence(self):
        decision = decide_preset_study(
            EqualQualityMatchResult(
                "insufficient_evidence", 1.0, None, "no pair", (), (), 0, 0
            )
        )
        self.assertEqual(decision.status, "insufficient_evidence")

