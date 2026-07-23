from dataclasses import dataclass
from typing import Iterable, Tuple

from .matching import EqualQualityMatchResult
from .roi import ROIRegionQuality


@dataclass(frozen=True)
class DenoiseStudyDecision:
    selected: bool
    decision: str
    reasons: Tuple[str, ...]
    checks: dict

    def to_dict(self) -> dict:
        return {
            "selected": self.selected,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "checks": self.checks,
        }


def decide_denoise_selection(
    match: EqualQualityMatchResult,
    region_quality: Iterable[ROIRegionQuality],
) -> DenoiseStudyDecision:
    region_quality = tuple(region_quality)
    pair = match.pair
    checks = {
        "equal_quality_pair": pair is not None,
        "global_quality": bool(
            pair and pair.baseline.quality_pass and pair.optimized.quality_pass
        ),
        "global_vmaf_delta": bool(
            pair and pair.vmaf_delta <= match.max_vmaf_delta + 1e-9
        ),
        "denoise_speed": bool(pair and pair.optimized.speed_pass),
        "critical_regions": all(
            item.quality_pass
            for item in region_quality
            if item.region.role == "critical"
        ),
        "evidence_regions": all(
            item.quality_pass
            for item in region_quality
            if item.region.role == "evidence"
        ),
        "average_bitrate_strictly_lower": bool(
            pair and pair.optimized.bitrate_bps < pair.baseline.bitrate_bps
        ),
    }
    labels = {
        "equal_quality_pair": match.reason,
        "global_quality": "降噪候选的全局画质未通过模式门槛",
        "global_vmaf_delta": "全局 VMAF 差超过配对容差",
        "denoise_speed": "降噪候选编码速度未通过模式门槛",
        "critical_regions": "至少一个 critical 区域的 VMAF/P5 下降超限",
        "evidence_regions": "至少一个 evidence 区域的 SSIM 下降超限",
        "average_bitrate_strictly_lower": "降噪候选的平均视频包码率没有严格降低",
    }
    reasons = tuple(labels[key] for key, passed in checks.items() if not passed)
    selected = all(checks.values())
    return DenoiseStudyDecision(
        selected=selected,
        decision="denoise_selected" if selected else "no_denoise_fallback",
        reasons=reasons,
        checks=checks,
    )
