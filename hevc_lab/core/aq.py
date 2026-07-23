from dataclasses import dataclass
from typing import Iterable, Optional

from .matching import EqualQualityMatchResult
from .models import AdaptiveQuantizationSettings
from .search import QualitySearchResult


@dataclass(frozen=True)
class AdaptiveQuantizationTrial:
    profile: AdaptiveQuantizationSettings
    search: QualitySearchResult
    match: EqualQualityMatchResult

    @property
    def bitrate_beneficial(self) -> bool:
        return bool(
            self.match.pair
            and self.match.pair.optimized.eligible
            and self.match.pair.algorithm_saving_pct > 0
        )

    def to_dict(self) -> dict:
        return {
            "profile": self.profile.to_dict(),
            "search": self.search.to_dict(),
            "match": self.match.to_dict(),
            "bitrate_beneficial": self.bitrate_beneficial,
        }


def select_best_aq_trial(
    trials: Iterable[AdaptiveQuantizationTrial],
    min_saving_pct: float = 0.0,
) -> Optional[AdaptiveQuantizationTrial]:
    if min_saving_pct < 0:
        raise ValueError("AQ 最小节省门槛不能小于 0")
    eligible = [
        trial
        for trial in trials
        if trial.bitrate_beneficial
        and trial.match.pair.algorithm_saving_pct > min_saving_pct
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda trial: (
            trial.match.pair.algorithm_saving_pct,
            -trial.match.pair.vmaf_delta,
            trial.match.pair.optimized.encode_speed_x,
        ),
    )
