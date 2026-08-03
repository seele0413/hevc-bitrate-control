from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


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


HEVC_CONFIG = FixedHevcConfig()


@dataclass(frozen=True)
class FixedBrowserPreviewConfig:
    codec: str = "libx264"
    preset: str = "ultrafast"
    crf: int = 26
    maxrate_mbps: float = 3.0
    bufsize_mbits: float = 6.0
    resolution: str = "source"
    gop_seconds: float = 1.0
    hls_segment_seconds: float = 1.0
    preview_only: bool = True
    included_in_bitrate_comparison: bool = False

    def public_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def ffmpeg_maxrate(self) -> str:
        return f"{self.maxrate_mbps:g}M"

    def ffmpeg_bufsize(self) -> str:
        return f"{self.bufsize_mbits:g}M"


BROWSER_PREVIEW_CONFIG = FixedBrowserPreviewConfig()
