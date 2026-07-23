from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class Toolchain:
    ffmpeg: Path
    ffprobe: Path
    vmaf_model: Path


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    codec: str
    width: int
    height: int
    fps: float
    duration_seconds: float
    video_bitrate_bps: float
    file_size_bytes: int
    pixel_format: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


@dataclass(frozen=True)
class PacketBitrateStats:
    packet_count: int
    packet_bytes: int
    duration_seconds: float
    average_bitrate_bps: float
    window_seconds: float
    window_bitrates_bps: Tuple[float, ...]
    peak_window_bitrate_bps: float
    p95_window_bitrate_bps: float

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["window_bitrates_bps"] = list(self.window_bitrates_bps)
        return data


@dataclass(frozen=True)
class ReferenceArtifact:
    input_path: Path
    input_sha256: str
    cache_key: str
    requested_start_seconds: float
    requested_duration_seconds: float
    effective_duration_seconds: float
    expected_frame_count: int
    frame_count: int
    video: VideoInfo
    timestamp_summary: Dict[str, Any]
    manifest_path: Path
    cache_hit: bool = False

    @property
    def path(self) -> Path:
        return self.video.path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_path": str(self.input_path),
            "input_sha256": self.input_sha256,
            "cache_key": self.cache_key,
            "requested_start_seconds": self.requested_start_seconds,
            "requested_duration_seconds": self.requested_duration_seconds,
            "effective_duration_seconds": self.effective_duration_seconds,
            "expected_frame_count": self.expected_frame_count,
            "frame_count": self.frame_count,
            "video": self.video.to_dict(),
            "timestamp_summary": self.timestamp_summary,
            "manifest_path": str(self.manifest_path),
            "cache_hit": self.cache_hit,
        }


@dataclass(frozen=True)
class InterConfig:
    name: str
    title: str
    description: str
    ref: int
    bframes: int
    b_adapt: int
    lookahead: int
    gop_seconds: float
    min_gop_seconds: float
    b_pyramid: int = 1
    scenecut: int = 40
    cutree: int = 1
    weightp: int = 1

    def x265_params(self, fps: float) -> str:
        keyint = max(1, round(fps * self.gop_seconds))
        min_keyint = max(1, min(keyint, round(fps * self.min_gop_seconds)))
        values = {
            "ref": self.ref,
            "bframes": self.bframes,
            "b-adapt": self.b_adapt,
            "b-pyramid": self.b_pyramid,
            "rc-lookahead": self.lookahead,
            "keyint": keyint,
            "min-keyint": min_keyint,
            "scenecut": self.scenecut,
            "cutree": self.cutree,
            "weightp": self.weightp,
        }
        return ":".join(f"{key}={value}" for key, value in values.items())

    def to_dict(self, fps: float) -> Dict[str, Any]:
        data = asdict(self)
        data["x265_params"] = self.x265_params(fps)
        return data


@dataclass(frozen=True)
class EncoderConditions:
    encoder: str = "libx265"
    preset: str = "medium"
    profile: str = "main"
    pixel_format: str = "yuv420p"
    preset_source: str = "default"
    mode_default_preset: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def cache_identity(self) -> Dict[str, str]:
        """只返回会改变编码输出的条件，排除报告来源元数据。"""
        return {
            "encoder": self.encoder,
            "preset": self.preset,
            "profile": self.profile,
            "pixel_format": self.pixel_format,
        }


@dataclass(frozen=True)
class RateControlSettings:
    vbv_maxrate_kbps: Optional[int] = None
    vbv_bufsize_kbits: Optional[int] = None
    vbv_init: float = 0.9
    const_vbv: bool = True

    def __post_init__(self) -> None:
        enabled_values = (self.vbv_maxrate_kbps, self.vbv_bufsize_kbits)
        if any(value is not None for value in enabled_values) and not all(
            value is not None for value in enabled_values
        ):
            raise ValueError("CRF 模式启用 VBV 时必须同时设置 maxrate 和 bufsize")
        if self.enabled:
            if self.vbv_maxrate_kbps <= 0 or self.vbv_bufsize_kbits <= 0:
                raise ValueError("VBV maxrate 和 bufsize 必须大于 0")
            if not 0 <= self.vbv_init <= 1:
                raise ValueError("VBV 初始占用比例必须在 0～1 之间")

    @property
    def enabled(self) -> bool:
        return self.vbv_maxrate_kbps is not None

    def x265_params(self) -> str:
        if not self.enabled:
            return ""
        values = {
            "vbv-maxrate": self.vbv_maxrate_kbps,
            "vbv-bufsize": self.vbv_bufsize_kbits,
            "vbv-init": self.vbv_init,
            "const-vbv": 1 if self.const_vbv else 0,
        }
        return ":".join(f"{key}={value}" for key, value in values.items())

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "enabled": self.enabled}


@dataclass(frozen=True)
class AdaptiveQuantizationSettings:
    name: str
    title: str
    description: str
    aq_mode: int
    aq_strength: float
    qg_size: int
    aq_motion: bool = False

    def __post_init__(self) -> None:
        if self.aq_mode not in (0, 1, 2, 3, 4):
            raise ValueError("AQ mode 必须为 0～4")
        if not 0 <= self.aq_strength <= 3:
            raise ValueError("AQ strength 必须在 0～3 之间")
        if self.qg_size not in (8, 16, 32, 64):
            raise ValueError("AQ qg-size 必须为 8、16、32 或 64")

    def x265_params(self) -> str:
        values = {
            "aq-mode": self.aq_mode,
            "aq-strength": self.aq_strength,
            "qg-size": self.qg_size,
            "aq-motion": 1 if self.aq_motion else 0,
        }
        return ":".join(f"{key}={value}" for key, value in values.items())

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["x265_params"] = self.x265_params()
        return data


ROI_ROLES: Tuple[str, str, str, str] = (
    "critical",
    "evidence",
    "normal",
    "discard",
)


@dataclass(frozen=True)
class ROIRegion:
    region_id: str
    title: str
    role: str
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if not isinstance(self.region_id, str) or not self.region_id.strip():
            raise ValueError("ROI 区域 ID 不能为空")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError(f"ROI 区域 {self.region_id!r} 的名称不能为空")
        if self.role not in ROI_ROLES:
            raise ValueError(
                f"ROI 区域 {self.region_id!r} 的角色 {self.role!r} 非法"
            )
        values = (self.x, self.y, self.width, self.height)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError(f"ROI 区域 {self.region_id!r} 的坐标和尺寸必须为整数")
        if self.x < 0 or self.y < 0:
            raise ValueError(f"ROI 区域 {self.region_id!r} 的 x/y 不能小于 0")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"ROI 区域 {self.region_id!r} 的宽高必须大于 0")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def aligned_rect(
        self,
        frame_width: int,
        frame_height: int,
        block_size: int = 16,
    ) -> Tuple[int, int, int, int]:
        """Return the effective outward-rounded block rectangle used by libx265."""
        if block_size <= 0:
            raise ValueError("ROI 对齐块尺寸必须大于 0")
        left = self.x // block_size * block_size
        top = self.y // block_size * block_size
        right = min(
            frame_width,
            ((self.right + block_size - 1) // block_size) * block_size,
        )
        bottom = min(
            frame_height,
            ((self.bottom + block_size - 1) // block_size) * block_size,
        )
        return left, top, right - left, bottom - top

    def to_dict(
        self,
        frame_width: Optional[int] = None,
        frame_height: Optional[int] = None,
    ) -> Dict[str, Any]:
        data = asdict(self)
        if frame_width is not None and frame_height is not None:
            data["effective_16x16_rect"] = list(
                self.aligned_rect(frame_width, frame_height)
            )
        return data


@dataclass(frozen=True)
class ROIQuantizationPolicy:
    mode: str
    critical: int
    evidence: int
    normal: int
    discard: int

    def __post_init__(self) -> None:
        for role in ROI_ROLES:
            value = getattr(self, role)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"ROI {role} QP 偏移必须为整数")
            if not -51 <= value <= 51:
                raise ValueError(f"ROI {role} QP 偏移必须在 -51～51 之间")

    def qp_delta(self, role: str) -> int:
        if role not in ROI_ROLES:
            raise ValueError(f"未知 ROI 角色：{role}")
        return int(getattr(self, role))

    def qoffset(self, role: str) -> float:
        return self.qp_delta(role) / 51.0

    def qoffset_expression(self, role: str) -> str:
        return f"{self.qp_delta(role)}/51"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["qoffsets"] = {role: self.qoffset(role) for role in ROI_ROLES}
        return data


@dataclass(frozen=True)
class ROISettings:
    version: int
    camera_id: str
    reference_width: int
    reference_height: int
    regions: Tuple[ROIRegion, ...]
    policy: ROIQuantizationPolicy
    config_hash: str

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version <= 0:
            raise ValueError("ROI 配置版本必须为正整数")
        if not isinstance(self.camera_id, str) or not self.camera_id.strip():
            raise ValueError("ROI camera_id 不能为空")
        resolution = (self.reference_width, self.reference_height)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in resolution
        ):
            raise ValueError("ROI 参考分辨率必须大于 0")
        if not self.regions:
            raise ValueError("ROI 配置至少需要一个区域")
        region_ids = [region.region_id for region in self.regions]
        if len(region_ids) != len(set(region_ids)):
            raise ValueError("ROI 区域 ID 不能重复")
        for region in self.regions:
            if region.right > self.reference_width or region.bottom > self.reference_height:
                raise ValueError(
                    f"ROI 区域 {region.region_id!r} 越出参考分辨率 "
                    f"{self.reference_width}x{self.reference_height}"
                )
        if not isinstance(self.config_hash, str) or not self.config_hash.strip():
            raise ValueError("ROI 配置哈希不能为空")

    def validate_input(self, width: int, height: int) -> None:
        if (width, height) != (self.reference_width, self.reference_height):
            raise ValueError(
                f"ROI 配置参考分辨率为 "
                f"{self.reference_width}x{self.reference_height}，"
                f"但输入为 {width}x{height}；静态 ROI 不允许静默缩放"
            )

    def ordered_regions(self) -> Tuple[ROIRegion, ...]:
        priority = {"evidence": 0, "critical": 1, "discard": 2, "normal": 3}
        return tuple(sorted(self.regions, key=lambda item: priority[item.role]))

    def filter_entries(self) -> Tuple[Tuple[ROIRegion, int], ...]:
        ordered = [
            (region, self.policy.qp_delta(region.role))
            for region in self.ordered_regions()
        ]
        fallback = ROIRegion(
            region_id="__normal_fallback__",
            title="全画面 normal 兜底",
            role="normal",
            x=0,
            y=0,
            width=self.reference_width,
            height=self.reference_height,
        )
        ordered.append((fallback, self.policy.normal))
        return tuple(ordered)

    def filter_chain(self) -> str:
        filters = []
        for index, (region, qp_delta) in enumerate(self.filter_entries()):
            clear = ":clear=1" if index == 0 else ""
            filters.append(
                "addroi="
                f"x={region.x}:y={region.y}:w={region.width}:h={region.height}:"
                f"qoffset={qp_delta}/51{clear}"
            )
        return ",".join(filters)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "camera_id": self.camera_id,
            "reference_resolution": {
                "width": self.reference_width,
                "height": self.reference_height,
            },
            "config_hash": self.config_hash,
            "regions": [
                region.to_dict(self.reference_width, self.reference_height)
                for region in self.regions
            ],
            "policy": self.policy.to_dict(),
            "filter_order": [region.region_id for region, _ in self.filter_entries()],
            "filter_chain": self.filter_chain(),
        }

    def cache_identity(self) -> Dict[str, Any]:
        return {
            "config_hash": self.config_hash,
            "regions": [region.to_dict() for region in self.regions],
            "policy": self.policy.to_dict(),
        }


@dataclass(frozen=True)
class DenoiseStrength:
    luma_spatial: float
    chroma_spatial: float
    luma_temporal: float
    chroma_temporal: float

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"降噪参数 {name} 必须为数字")
            if value < 0:
                raise ValueError(f"降噪参数 {name} 不能小于 0")

    @property
    def enabled(self) -> bool:
        return any(value > 0 for value in asdict(self).values())

    def filter_expression(self) -> str:
        if not self.enabled:
            return "null"
        values = (
            self.luma_spatial,
            self.chroma_spatial,
            self.luma_temporal,
            self.chroma_temporal,
        )
        return "hqdn3d=" + ":".join(f"{value:g}" for value in values)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "enabled": self.enabled,
            "filter_expression": self.filter_expression(),
        }


@dataclass(frozen=True)
class DenoisePolicy:
    mode: str
    critical: DenoiseStrength
    evidence: DenoiseStrength
    normal: DenoiseStrength
    discard: DenoiseStrength

    def strength_for(self, role: str) -> DenoiseStrength:
        if role not in ROI_ROLES:
            raise ValueError(f"未知 ROI 角色：{role}")
        return getattr(self, role)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "critical": self.critical.to_dict(),
            "evidence": self.evidence.to_dict(),
            "normal": self.normal.to_dict(),
            "discard": self.discard.to_dict(),
        }


@dataclass(frozen=True)
class DenoiseSettings:
    roi: ROISettings
    policy: DenoisePolicy

    def validate_input(self, width: int, height: int) -> None:
        self.roi.validate_input(width, height)

    def overlay_regions(self) -> Tuple[ROIRegion, ...]:
        priority = {"discard": 0, "critical": 1, "evidence": 2}
        return tuple(
            sorted(
                (region for region in self.roi.regions if region.role != "normal"),
                key=lambda item: priority[item.role],
            )
        )

    def filter_complex(self, output_label: str = "denoised") -> str:
        regions = self.overlay_regions()
        if not regions:
            return (
                f"[0:v]{self.policy.normal.filter_expression()},"
                f"format=yuv420p[{output_label}]"
            )
        split_labels = ["base_src"] + [f"patch_{index}_src" for index in range(len(regions))]
        graph = [
            f"[0:v]split={len(split_labels)}"
            + "".join(f"[{label}]" for label in split_labels)
        ]
        graph.append(
            f"[base_src]{self.policy.normal.filter_expression()}[stage_0]"
        )
        for index, region in enumerate(regions):
            strength = self.policy.strength_for(region.role)
            graph.append(
                f"[patch_{index}_src]crop={region.width}:{region.height}:"
                f"{region.x}:{region.y},{strength.filter_expression()}[patch_{index}]"
            )
            graph.append(
                f"[stage_{index}][patch_{index}]overlay=x={region.x}:y={region.y}:"
                f"eof_action=pass:shortest=1[stage_{index + 1}]"
            )
        graph.append(f"[stage_{len(regions)}]format=yuv420p[{output_label}]")
        return ";".join(graph)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roi_config_hash": self.roi.config_hash,
            "camera_id": self.roi.camera_id,
            "reference_resolution": {
                "width": self.roi.reference_width,
                "height": self.roi.reference_height,
            },
            "regions": [region.to_dict() for region in self.roi.regions],
            "policy": self.policy.to_dict(),
            "overlay_order": [region.region_id for region in self.overlay_regions()],
            "filter_complex": self.filter_complex(),
        }

    def cache_identity(self) -> Dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class ModePolicy:
    name: str
    title: str
    description: str
    priority: str
    preset: str
    default_crf: float
    target_vmaf: float
    target_vmaf_p5: float
    target_ssim: float
    min_speed_x: Optional[float]
    min_algorithm_saving_pct: float
    min_source_saving_pct: float
    vbv_peak_ratio: float
    vbv_buffer_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "speed_gate_enabled": self.min_speed_x is not None,
        }


@dataclass(frozen=True)
class ComparisonPlan:
    mode: ModePolicy
    conditions: EncoderConditions
    baseline: InterConfig
    optimized: InterConfig

    @property
    def schemes(self) -> Tuple[InterConfig, InterConfig]:
        return self.baseline, self.optimized

    def to_dict(self, fps: float) -> Dict[str, Any]:
        return {
            "mode": self.mode.to_dict(),
            "conditions": self.conditions.to_dict(),
            "baseline": self.baseline.to_dict(fps),
            "optimized": self.optimized.to_dict(fps),
        }


@dataclass
class CandidateResult:
    name: str
    title: str
    description: str
    output_path: str
    x265_params: str
    crf: float
    preset: str
    bitrate_bps: float
    file_size_bytes: int
    vmaf_mean: float
    vmaf_p5: float
    ssim: float
    encode_seconds: float
    encode_speed_x: float
    quality_pass: bool
    speed_pass: bool
    eligible: bool
    bitrate_saving_vs_source_pct: float = 0.0
    bitrate_saving_vs_baseline_pct: float = 0.0
    cache_hit: bool = False
    failure: Optional[str] = None
    speed_tier: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
