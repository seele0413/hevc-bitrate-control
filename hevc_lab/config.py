from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class FixedDenoiseConfig:
    enabled: bool = True
    filter_name: str = "hqdn3d"
    profile: str = "light_detail_preserving"
    luma_spatial: float = 1.5
    chroma_spatial: float = 1.0
    luma_temporal: float = 2.5
    chroma_temporal: float = 2.0
    placement: str = "after_h264_decode_before_h265_frame_queue"

    def ffmpeg_filter(self) -> str:
        return (
            f"{self.filter_name}="
            f"{self.luma_spatial:.1f}:{self.chroma_spatial:.1f}:"
            f"{self.luma_temporal:.1f}:{self.chroma_temporal:.1f}"
        )

    def public_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "filter": self.filter_name,
            "profile": self.profile,
            "luma_spatial": self.luma_spatial,
            "chroma_spatial": self.chroma_spatial,
            "luma_temporal": self.luma_temporal,
            "chroma_temporal": self.chroma_temporal,
            "placement": self.placement,
        }


@dataclass(frozen=True)
class FixedHevcConfig:
    crf: float = 36.0
    preset: str = "fast"
    profile: str = "main"
    pixel_format: str = "yuv420p"
    ref: int = 4
    bframes: int = 4
    b_adapt: int = 2
    lookahead: int = 45
    gop_seconds: int = 10
    min_gop_seconds: int = 2
    scenecut: int = 40
    cutree: int = 1
    weightp: int = 1
    aq_mode: int = 2
    aq_strength: float = 1.0
    qg_size: int = 32
    aq_motion: int = 0

    def x265_params(self, fps: float) -> str:
        keyint = max(1, round(fps * self.gop_seconds))
        min_keyint = max(1, round(fps * self.min_gop_seconds))
        values = {
            "ref": self.ref,
            "bframes": self.bframes,
            "b-adapt": self.b_adapt,
            "rc-lookahead": self.lookahead,
            "keyint": keyint,
            "min-keyint": min_keyint,
            "scenecut": self.scenecut,
            "cutree": self.cutree,
            "weightp": self.weightp,
            "aq-mode": self.aq_mode,
            "aq-strength": self.aq_strength,
            "qg-size": self.qg_size,
            "aq-motion": self.aq_motion,
        }
        return ":".join(f"{key}={value}" for key, value in values.items())

    def public_dict(self, fps: Optional[float] = None) -> Dict[str, Any]:
        result = asdict(self)
        if fps is not None:
            result["keyint_frames"] = max(1, round(fps * self.gop_seconds))
            result["min_keyint_frames"] = max(1, round(fps * self.min_gop_seconds))
            result["x265_params"] = self.x265_params(fps)
        return result


DENOISE_CONFIG = FixedDenoiseConfig()
HEVC_CONFIG = FixedHevcConfig()
