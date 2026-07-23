import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

from .configs import roi_quantization_policy
from .matching import EqualQualityMatchResult
from .models import ROIRegion, ROISettings


@dataclass(frozen=True)
class RegionQualityMetrics:
    vmaf_mean: float
    vmaf_p5: float
    ssim: float
    cache_hit: bool = False

    def to_dict(self) -> dict:
        return {
            "vmaf_mean": self.vmaf_mean,
            "vmaf_p5": self.vmaf_p5,
            "ssim": self.ssim,
            "cache_hit": self.cache_hit,
        }


@dataclass(frozen=True)
class ROIRegionQuality:
    region: ROIRegion
    control: RegionQualityMetrics
    roi: RegionQualityMetrics
    vmaf_drop: float
    vmaf_p5_drop: float
    ssim_drop: float
    quality_pass: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "region": self.region.to_dict(),
            "control": self.control.to_dict(),
            "roi": self.roi.to_dict(),
            "vmaf_drop": self.vmaf_drop,
            "vmaf_p5_drop": self.vmaf_p5_drop,
            "ssim_drop": self.ssim_drop,
            "quality_pass": self.quality_pass,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ROIStudyDecision:
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


def _canonical_config(payload: dict) -> dict:
    try:
        version = payload["version"]
        camera_id = payload["camera_id"]
        resolution = payload["reference_resolution"]
        regions = payload["regions"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"ROI 配置缺少必填字段：{exc}") from exc
    if not isinstance(regions, list):
        raise ValueError("ROI regions 必须为数组")
    canonical_regions = []
    for index, item in enumerate(regions):
        if not isinstance(item, dict):
            raise ValueError(f"ROI regions[{index}] 必须为对象")
        try:
            canonical_regions.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "role": item["role"],
                    "x": item["x"],
                    "y": item["y"],
                    "width": item["width"],
                    "height": item["height"],
                }
            )
        except KeyError as exc:
            raise ValueError(f"ROI regions[{index}] 缺少字段：{exc}") from exc
    try:
        reference_resolution = {
            "width": resolution["width"],
            "height": resolution["height"],
        }
    except (KeyError, TypeError) as exc:
        raise ValueError(f"ROI reference_resolution 无效：{exc}") from exc
    return {
        "version": version,
        "camera_id": camera_id,
        "reference_resolution": reference_resolution,
        "regions": canonical_regions,
    }


def load_roi_settings(path: Path, mode: str = "balanced") -> ROISettings:
    path = path.expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"ROI 配置不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"ROI 配置不是有效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("ROI 配置顶层必须为对象")
    canonical = _canonical_config(payload)
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    regions = tuple(
        ROIRegion(
            region_id=item["id"],
            title=item["title"],
            role=item["role"],
            x=item["x"],
            y=item["y"],
            width=item["width"],
            height=item["height"],
        )
        for item in canonical["regions"]
    )
    resolution = canonical["reference_resolution"]
    return ROISettings(
        version=canonical["version"],
        camera_id=canonical["camera_id"],
        reference_width=resolution["width"],
        reference_height=resolution["height"],
        regions=regions,
        policy=roi_quantization_policy(mode),
        config_hash=config_hash,
    )


def compare_region_quality(
    region: ROIRegion,
    control: RegionQualityMetrics,
    roi: RegionQualityMetrics,
    critical_vmaf_drop_limit: float = 0.5,
    critical_p5_drop_limit: float = 1.0,
    evidence_ssim_drop_limit: float = 0.002,
) -> ROIRegionQuality:
    vmaf_drop = control.vmaf_mean - roi.vmaf_mean
    vmaf_p5_drop = control.vmaf_p5 - roi.vmaf_p5
    ssim_drop = control.ssim - roi.ssim
    if region.role == "critical":
        quality_pass = (
            vmaf_drop <= critical_vmaf_drop_limit + 1e-9
            and vmaf_p5_drop <= critical_p5_drop_limit + 1e-9
        )
        reason = (
            f"critical 要求 VMAF 下降≤{critical_vmaf_drop_limit:.3f}、"
            f"P5 下降≤{critical_p5_drop_limit:.3f}"
        )
    elif region.role == "evidence":
        quality_pass = ssim_drop <= evidence_ssim_drop_limit + 1e-9
        reason = f"evidence 要求 SSIM 下降≤{evidence_ssim_drop_limit:.6f}"
    else:
        quality_pass = True
        reason = "该角色不作为 ROI 选择的局部画质门槛"
    return ROIRegionQuality(
        region=region,
        control=control,
        roi=roi,
        vmaf_drop=vmaf_drop,
        vmaf_p5_drop=vmaf_p5_drop,
        ssim_drop=ssim_drop,
        quality_pass=quality_pass,
        reason=reason,
    )


def decide_roi_selection(
    match: EqualQualityMatchResult,
    region_quality: Iterable[ROIRegionQuality],
) -> ROIStudyDecision:
    region_quality = tuple(region_quality)
    pair = match.pair
    checks = {
        "equal_quality_pair": pair is not None,
        "global_quality": bool(
            pair and pair.baseline.quality_pass and pair.optimized.quality_pass
        ),
        "global_vmaf_delta": bool(pair and pair.vmaf_delta <= match.max_vmaf_delta + 1e-9),
        "roi_speed": bool(pair and pair.optimized.speed_pass),
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
    reasons = []
    labels = {
        "equal_quality_pair": match.reason,
        "global_quality": "全局画质未通过模式绝对门槛",
        "global_vmaf_delta": "全局 VMAF 差超过配对容差",
        "roi_speed": "ROI 候选编码速度未通过模式门槛",
        "critical_regions": "至少一个 critical 区域的 VMAF/P5 下降超限",
        "evidence_regions": "至少一个 evidence 区域的 SSIM 下降超限",
        "average_bitrate_strictly_lower": "ROI 候选的平均视频包码率没有严格降低",
    }
    for key, passed in checks.items():
        if not passed:
            reasons.append(labels[key])
    selected = all(checks.values())
    return ROIStudyDecision(
        selected=selected,
        decision="roi_selected" if selected else "no_roi_fallback",
        reasons=tuple(reasons),
        checks=checks,
    )


def important_regions(settings: ROISettings) -> Tuple[ROIRegion, ...]:
    return tuple(
        region for region in settings.regions if region.role in ("critical", "evidence")
    )
