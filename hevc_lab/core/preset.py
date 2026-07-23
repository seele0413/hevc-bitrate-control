from dataclasses import dataclass
from typing import Optional

from .matching import EqualQualityMatchResult


@dataclass(frozen=True)
class PresetStudyDecision:
    status: str
    benefit_confirmed: bool
    saving_pct: Optional[float]
    reason: str

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "benefit_confirmed": self.benefit_confirmed,
            "saving_pct": self.saving_pct,
            "reason": self.reason,
        }


def decide_preset_study(match: EqualQualityMatchResult) -> PresetStudyDecision:
    if match.pair is None:
        return PresetStudyDecision(
            status="insufficient_evidence",
            benefit_confirmed=False,
            saving_pct=None,
            reason=f"medium 与 slow 未找到等画质配对：{match.reason}",
        )
    saving = match.pair.algorithm_saving_pct
    if saving > 0:
        return PresetStudyDecision(
            status="slow_benefit_confirmed",
            benefit_confirmed=True,
            saving_pct=saving,
            reason=f"slow 在等画质下相对 medium 降低平均视频包码率 {saving:.2f}%",
        )
    return PresetStudyDecision(
        status="slow_benefit_not_confirmed",
        benefit_confirmed=False,
        saving_pct=saving,
        reason=f"slow 在等画质下相对 medium 的码率变化为 {saving:.2f}%，未取得严格正收益",
    )
