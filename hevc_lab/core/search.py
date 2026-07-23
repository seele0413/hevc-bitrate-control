from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .models import CandidateResult


@dataclass(frozen=True)
class QualityThresholds:
    vmaf_mean: float
    vmaf_p5: float
    ssim: float

    def accepts(self, candidate: CandidateResult) -> bool:
        return (
            candidate.vmaf_mean >= self.vmaf_mean
            and candidate.vmaf_p5 >= self.vmaf_p5
            and candidate.ssim >= self.ssim
        )

    def to_dict(self) -> dict:
        return {
            "vmaf_mean": self.vmaf_mean,
            "vmaf_p5": self.vmaf_p5,
            "ssim": self.ssim,
        }


@dataclass(frozen=True)
class QualitySearchSpec:
    thresholds: QualityThresholds
    crf_min: float = 18.0
    crf_max: float = 38.0
    crf_step: float = 0.5
    anchors: Tuple[float, ...] = (18.0, 28.0, 38.0)

    def __post_init__(self) -> None:
        if self.crf_step <= 0:
            raise ValueError("CRF 搜索步长必须大于 0")
        if self.crf_max < self.crf_min:
            raise ValueError("CRF 搜索上限不能小于下限")
        span = (self.crf_max - self.crf_min) / self.crf_step
        if abs(span - round(span)) > 1e-8:
            raise ValueError("CRF 搜索范围必须能被步长整除")
        if not self.anchors:
            raise ValueError("CRF 搜索至少需要一个锚点")
        for anchor in self.anchors:
            offset = (anchor - self.crf_min) / self.crf_step
            if anchor < self.crf_min or anchor > self.crf_max:
                raise ValueError(f"CRF 锚点 {anchor} 超出搜索范围")
            if abs(offset - round(offset)) > 1e-8:
                raise ValueError(f"CRF 锚点 {anchor} 未落在搜索网格上")

    def grid(self) -> Tuple[float, ...]:
        count = round((self.crf_max - self.crf_min) / self.crf_step)
        return tuple(round(self.crf_min + index * self.crf_step, 8) for index in range(count + 1))

    def to_dict(self) -> dict:
        return {
            "crf_min": self.crf_min,
            "crf_max": self.crf_max,
            "crf_step": self.crf_step,
            "anchors": list(self.anchors),
            "thresholds": self.thresholds.to_dict(),
        }


@dataclass
class QualitySearchResult:
    spec: QualitySearchSpec
    points: List[CandidateResult]
    evaluation_order: List[float]
    selected: Optional[CandidateResult]
    monotonicity_violations: List[str]
    exhaustive_fallback: bool

    def to_dict(self) -> dict:
        return {
            "spec": self.spec.to_dict(),
            "evaluation_order": self.evaluation_order,
            "tested_count": len(self.points),
            "exhaustive_fallback": self.exhaustive_fallback,
            "monotonicity_violations": self.monotonicity_violations,
            "selected": self.selected.to_dict() if self.selected else None,
            "points": [point.to_dict() for point in sorted(self.points, key=lambda item: item.crf)],
        }


CandidateEvaluator = Callable[[float], CandidateResult]


def _monotonicity_violations(points: Dict[int, CandidateResult]) -> List[str]:
    """检测足以改变搜索结论的明显反常，不追究正常的指标微小抖动。"""
    violations: List[str] = []
    ordered = sorted(points.items())
    for (_, lower), (_, higher) in zip(ordered, ordered[1:]):
        if not lower.quality_pass and higher.quality_pass:
            violations.append(
                f"CRF {lower.crf:.1f} 不合格，但更高的 CRF {higher.crf:.1f} 合格"
            )
        metric_reversals = []
        if higher.vmaf_mean > lower.vmaf_mean + 0.5:
            metric_reversals.append("VMAF")
        if higher.vmaf_p5 > lower.vmaf_p5 + 1.0:
            metric_reversals.append("VMAF P5")
        if higher.ssim > lower.ssim + 0.001:
            metric_reversals.append("SSIM")
        if metric_reversals:
            violations.append(
                f"CRF {lower.crf:.1f}→{higher.crf:.1f} 的 "
                f"{'/'.join(metric_reversals)} 明显反向上升"
            )
    return violations


def adaptive_quality_search(
    evaluate: CandidateEvaluator,
    spec: QualitySearchSpec,
) -> QualitySearchResult:
    """寻找满足全部画质门槛的最高 CRF，并复核相邻 0.5 CRF 点。

    正常情况下先评估 18/28/38 三个锚点，再在合格与不合格边界间
    二分。若已测点出现明显非单调，则补测完整网格，防止遗漏反常的
    合格区间。
    """
    grid = spec.grid()
    index_by_crf = {value: index for index, value in enumerate(grid)}
    points: Dict[int, CandidateResult] = {}
    evaluation_order: List[float] = []

    def evaluate_index(index: int) -> CandidateResult:
        if index in points:
            return points[index]
        crf = grid[index]
        candidate = evaluate(crf)
        if abs(candidate.crf - crf) > 1e-8:
            raise ValueError(
                f"评估器返回 CRF {candidate.crf}，但搜索请求的是 {crf}"
            )
        candidate.quality_pass = spec.thresholds.accepts(candidate)
        candidate.eligible = candidate.quality_pass and candidate.speed_pass
        points[index] = candidate
        evaluation_order.append(crf)
        return candidate

    anchor_indices = [index_by_crf[round(value, 8)] for value in spec.anchors]
    for index in anchor_indices:
        evaluate_index(index)

    exhaustive_fallback = bool(_monotonicity_violations(points))
    if not exhaustive_fallback:
        passing_indices = [index for index, point in points.items() if point.quality_pass]
        if passing_indices:
            low = max(passing_indices)
            if low < len(grid) - 1:
                failing_above = sorted(
                    index
                    for index, point in points.items()
                    if index > low and not point.quality_pass
                )
                if not failing_above:
                    evaluate_index(len(grid) - 1)
                    if points[len(grid) - 1].quality_pass:
                        low = len(grid) - 1
                    else:
                        failing_above = [len(grid) - 1]
                if low < len(grid) - 1 and failing_above:
                    high = failing_above[0]
                    while high - low > 1:
                        middle = (low + high) // 2
                        if evaluate_index(middle).quality_pass:
                            low = middle
                        else:
                            high = middle

    passing_indices = [index for index, point in points.items() if point.quality_pass]
    if passing_indices:
        selected_index = max(passing_indices)
        for neighbor in (selected_index - 1, selected_index + 1):
            if 0 <= neighbor < len(grid):
                evaluate_index(neighbor)

    violations = _monotonicity_violations(points)
    if violations:
        exhaustive_fallback = True
    if exhaustive_fallback:
        for index in range(len(grid)):
            evaluate_index(index)
        violations = _monotonicity_violations(points)

    passing_indices = [index for index, point in points.items() if point.quality_pass]
    selected = points[max(passing_indices)] if passing_indices else None
    if selected is not None:
        selected_index = index_by_crf[round(selected.crf, 8)]
        for neighbor in (selected_index - 1, selected_index + 1):
            if 0 <= neighbor < len(grid):
                evaluate_index(neighbor)

    return QualitySearchResult(
        spec=spec,
        points=list(points.values()),
        evaluation_order=evaluation_order,
        selected=selected,
        monotonicity_violations=violations,
        exhaustive_fallback=exhaustive_fallback,
    )
