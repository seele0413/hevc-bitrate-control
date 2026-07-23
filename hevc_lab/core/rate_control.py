import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import CandidateResult, ModePolicy, PacketBitrateStats, RateControlSettings
from .search import QualityThresholds
from .selection import calculate_saving


@dataclass(frozen=True)
class RateControlTrial:
    peak_ratio: float
    settings: RateControlSettings
    candidate: CandidateResult
    packet_stats: PacketBitrateStats
    vmaf_delta: float
    quality_preserved: bool
    bitrate_beneficial: bool

    def to_dict(self, uncapped_stats: PacketBitrateStats) -> dict:
        return {
            "peak_ratio": self.peak_ratio,
            "settings": self.settings.to_dict(),
            "candidate": self.candidate.to_dict(),
            "packet_bitrate": self.packet_stats.to_dict(),
            "vmaf_delta": self.vmaf_delta,
            "quality_preserved": self.quality_preserved,
            "bitrate_beneficial": self.bitrate_beneficial,
            "average_saving_vs_uncapped_pct": calculate_saving(
                uncapped_stats.average_bitrate_bps,
                self.packet_stats.average_bitrate_bps,
            ),
            "peak_saving_vs_uncapped_pct": calculate_saving(
                uncapped_stats.peak_window_bitrate_bps,
                self.packet_stats.peak_window_bitrate_bps,
            ),
        }


def derive_vbv_settings(
    average_bitrate_bps: float,
    peak_ratio: float,
    buffer_seconds: float,
) -> RateControlSettings:
    if average_bitrate_bps <= 0:
        raise ValueError("生成 VBV 参数需要有效的平均码率")
    if peak_ratio < 1:
        raise ValueError("VBV 峰值倍率不能小于 1")
    if buffer_seconds <= 0:
        raise ValueError("VBV 缓冲时长必须大于 0")
    maxrate_kbps = max(1, math.ceil(average_bitrate_bps * peak_ratio / 1000.0))
    bufsize_kbits = max(1, math.ceil(maxrate_kbps * buffer_seconds))
    return RateControlSettings(
        vbv_maxrate_kbps=maxrate_kbps,
        vbv_bufsize_kbits=bufsize_kbits,
        vbv_init=0.9,
        const_vbv=True,
    )


def vbv_ratio_candidates(
    policy: ModePolicy,
    maximum_ratio: float = 2.5,
    step: float = 0.25,
) -> Tuple[float, ...]:
    if step <= 0:
        raise ValueError("VBV 放宽步长必须大于 0")
    start = policy.vbv_peak_ratio
    if start > maximum_ratio:
        raise ValueError("最大峰值倍率不能低于所选模式的初始倍率")
    count = math.ceil((maximum_ratio - start) / step)
    values = [round(start + index * step, 4) for index in range(count + 1)]
    if values[-1] < maximum_ratio:
        values.append(round(maximum_ratio, 4))
    return tuple(dict.fromkeys(values))


def quality_is_preserved(
    candidate: CandidateResult,
    uncapped: CandidateResult,
    thresholds: QualityThresholds,
    max_vmaf_delta: float = 1.0,
) -> bool:
    return (
        thresholds.accepts(candidate)
        and abs(candidate.vmaf_mean - uncapped.vmaf_mean) <= max_vmaf_delta + 1e-9
    )


def first_quality_preserving_trial(
    trials: List[RateControlTrial],
) -> Optional[RateControlTrial]:
    return next(
        (
            trial
            for trial in trials
            if trial.quality_preserved and trial.bitrate_beneficial
        ),
        None,
    )
