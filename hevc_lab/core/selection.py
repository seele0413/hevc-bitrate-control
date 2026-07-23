from typing import Iterable, Optional

from .models import CandidateResult


def calculate_saving(reference_bps: float, candidate_bps: float) -> float:
    if reference_bps <= 0:
        return 0.0
    return (1.0 - candidate_bps / reference_bps) * 100.0


def select_candidate(candidates: Iterable[CandidateResult]) -> Optional[CandidateResult]:
    eligible = [candidate for candidate in candidates if candidate.eligible]
    return min(eligible, key=lambda item: (item.bitrate_bps, -item.encode_speed_x)) if eligible else None


def is_deployable(
    candidate: Optional[CandidateResult],
    min_source_saving_pct: float,
    min_algorithm_saving_pct: float = 0.0,
) -> bool:
    return bool(
        candidate
        and candidate.eligible
        and candidate.bitrate_saving_vs_baseline_pct > 0
        and candidate.bitrate_saving_vs_baseline_pct >= min_algorithm_saving_pct
        and candidate.bitrate_saving_vs_source_pct >= min_source_saving_pct
    )
