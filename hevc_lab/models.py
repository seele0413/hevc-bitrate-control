"""旧版顶层导入的兼容层；新代码应从 :mod:`hevc_lab.core.models` 导入。"""

from .core.models import (
    AdaptiveQuantizationSettings,
    CandidateResult,
    ComparisonPlan,
    EncoderConditions,
    InterConfig,
    ModePolicy,
    PacketBitrateStats,
    RateControlSettings,
    ReferenceArtifact,
    Toolchain,
    VideoInfo,
)

__all__ = [
    "AdaptiveQuantizationSettings",
    "CandidateResult",
    "ComparisonPlan",
    "EncoderConditions",
    "InterConfig",
    "ModePolicy",
    "PacketBitrateStats",
    "RateControlSettings",
    "ReferenceArtifact",
    "Toolchain",
    "VideoInfo",
]
