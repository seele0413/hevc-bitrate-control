from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import CandidateResult
from .search import QualitySearchResult
from .selection import calculate_saving


@dataclass(frozen=True)
class EqualQualityPair:
    baseline: CandidateResult
    optimized: CandidateResult
    vmaf_delta: float
    vmaf_p5_delta: float
    ssim_delta: float
    algorithm_saving_pct: float

    def to_dict(self) -> dict:
        return {
            "baseline": self.baseline.to_dict(),
            "optimized": self.optimized.to_dict(),
            "vmaf_delta": self.vmaf_delta,
            "vmaf_p5_delta": self.vmaf_p5_delta,
            "ssim_delta": self.ssim_delta,
            "algorithm_saving_pct": self.algorithm_saving_pct,
        }


@dataclass(frozen=True)
class EqualQualityMatchResult:
    status: str
    max_vmaf_delta: float
    pair: Optional[EqualQualityPair]
    reason: str
    baseline_boundary_crfs: Tuple[float, ...]
    optimized_boundary_crfs: Tuple[float, ...]
    evaluated_pair_count: int
    qualifying_pair_count: int

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "max_vmaf_delta": self.max_vmaf_delta,
            "reason": self.reason,
            "baseline_boundary_crfs": list(self.baseline_boundary_crfs),
            "optimized_boundary_crfs": list(self.optimized_boundary_crfs),
            "evaluated_pair_count": self.evaluated_pair_count,
            "qualifying_pair_count": self.qualifying_pair_count,
            "pair": self.pair.to_dict() if self.pair else None,
        }


def boundary_quality_candidates(search: QualitySearchResult) -> List[CandidateResult]:
    """只返回最高合格 CRF 及其低一档相邻合格点。

    搜索锚点中的低 CRF 视频通常远高于目标画质，用它们配对虽然容易得到
    极小 VMAF 差，却违反“在画质门槛下最小化码率”的实验目标。
    """
    if search.selected is None:
        return []
    lower_bound = search.selected.crf - search.spec.crf_step - 1e-8
    return sorted(
        (
            point
            for point in search.points
            if point.eligible and point.crf >= lower_bound
        ),
        key=lambda item: item.crf,
        reverse=True,
    )


def match_equal_quality_candidates(
    baseline_search: QualitySearchResult,
    optimized_search: QualitySearchResult,
    max_vmaf_delta: float = 1.0,
) -> EqualQualityMatchResult:
    if max_vmaf_delta < 0:
        raise ValueError("最大 VMAF 差不能小于 0")
    baseline_candidates = boundary_quality_candidates(baseline_search)
    optimized_candidates = boundary_quality_candidates(optimized_search)
    baseline_crfs = tuple(item.crf for item in baseline_candidates)
    optimized_crfs = tuple(item.crf for item in optimized_candidates)
    pair_count = len(baseline_candidates) * len(optimized_candidates)
    if not baseline_candidates or not optimized_candidates:
        missing = []
        if not baseline_candidates:
            missing.append("工程基线")
        if not optimized_candidates:
            missing.append("优化组合")
        return EqualQualityMatchResult(
            status="insufficient_evidence",
            max_vmaf_delta=max_vmaf_delta,
            pair=None,
            reason=f"{'和'.join(missing)}在搜索范围内没有画质合格边界点",
            baseline_boundary_crfs=baseline_crfs,
            optimized_boundary_crfs=optimized_crfs,
            evaluated_pair_count=pair_count,
            qualifying_pair_count=0,
        )

    qualifying = []
    for baseline in baseline_candidates:
        for optimized in optimized_candidates:
            delta = abs(baseline.vmaf_mean - optimized.vmaf_mean)
            if delta <= max_vmaf_delta + 1e-9:
                qualifying.append((delta, baseline, optimized))
    if not qualifying:
        closest_delta = min(
            abs(baseline.vmaf_mean - optimized.vmaf_mean)
            for baseline in baseline_candidates
            for optimized in optimized_candidates
        )
        return EqualQualityMatchResult(
            status="insufficient_evidence",
            max_vmaf_delta=max_vmaf_delta,
            pair=None,
            reason=(
                f"边界候选最小 VMAF 差为 {closest_delta:.3f}，"
                f"超过允许的 {max_vmaf_delta:.3f}"
            ),
            baseline_boundary_crfs=baseline_crfs,
            optimized_boundary_crfs=optimized_crfs,
            evaluated_pair_count=pair_count,
            qualifying_pair_count=0,
        )

    _, baseline, optimized = min(
        qualifying,
        key=lambda item: (
            item[0],
            -(item[1].crf + item[2].crf),
            item[1].bitrate_bps + item[2].bitrate_bps,
        ),
    )
    pair = EqualQualityPair(
        baseline=baseline,
        optimized=optimized,
        vmaf_delta=abs(baseline.vmaf_mean - optimized.vmaf_mean),
        vmaf_p5_delta=abs(baseline.vmaf_p5 - optimized.vmaf_p5),
        ssim_delta=abs(baseline.ssim - optimized.ssim),
        algorithm_saving_pct=calculate_saving(
            baseline.bitrate_bps,
            optimized.bitrate_bps,
        ),
    )
    return EqualQualityMatchResult(
        status="matched",
        max_vmaf_delta=max_vmaf_delta,
        pair=pair,
        reason="边界候选中找到满足 VMAF 差门槛的最接近配对",
        baseline_boundary_crfs=baseline_crfs,
        optimized_boundary_crfs=optimized_crfs,
        evaluated_pair_count=pair_count,
        qualifying_pair_count=len(qualifying),
    )
