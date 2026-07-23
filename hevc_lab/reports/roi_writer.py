import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..core.matching import EqualQualityMatchResult
from ..core.models import (
    AdaptiveQuantizationSettings,
    EncoderConditions,
    InterConfig,
    ROISettings,
    ReferenceArtifact,
    Toolchain,
    VideoInfo,
)
from ..core.roi import ROIRegionQuality, ROIStudyDecision
from ..core.search import QualitySearchResult, QualityThresholds
from ..tools import run_process


def render_roi_overlay(
    toolchain: Toolchain,
    reference_path: Path,
    settings: ROISettings,
    destination: Path,
) -> Path:
    colors = {
        "critical": "red",
        "evidence": "yellow",
        "discard": "blue",
        "normal": "gray",
    }
    filters = []
    for region in settings.regions:
        color = colors[region.role]
        geometry = f"x={region.x}:y={region.y}:w={region.width}:h={region.height}"
        filters.append(f"drawbox={geometry}:color={color}@0.14:t=fill")
        filters.append(f"drawbox={geometry}:color={color}@0.95:t=4")
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_process(
        [
            toolchain.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            reference_path,
            "-vf",
            ",".join(filters),
            "-frames:v",
            "1",
            destination,
        ]
    )
    return destination


def write_roi_study_reports(
    output_dir: Path,
    source: VideoInfo,
    reference: ReferenceArtifact,
    mode: dict,
    scheme: InterConfig,
    conditions: EncoderConditions,
    thresholds: QualityThresholds,
    aq_profile: AdaptiveQuantizationSettings,
    settings: ROISettings,
    control_search: QualitySearchResult,
    roi_search: QualitySearchResult,
    match: EqualQualityMatchResult,
    region_quality: Iterable[ROIRegionQuality],
    decision: ROIStudyDecision,
    overlay_path: Path,
) -> dict:
    region_quality = tuple(region_quality)
    selected = match.pair.optimized.to_dict() if decision.selected and match.pair else None
    fallback = match.pair.baseline.to_dict() if not decision.selected and match.pair else None
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "study": "H.265 固定机位静态 ROI 等画质研究",
        "input": source.to_dict(),
        "reference": reference.to_dict(),
        "mode": mode,
        "scheme": scheme.to_dict(reference.video.fps),
        "encoder_conditions": conditions.to_dict(),
        "adaptive_quantization": aq_profile.to_dict(),
        "quality_thresholds": thresholds.to_dict(),
        "roi_settings": settings.to_dict(),
        "control_search": control_search.to_dict(),
        "roi_search": roi_search.to_dict(),
        "match": match.to_dict(),
        "region_quality": [item.to_dict() for item in region_quality],
        "decision": decision.to_dict(),
        "selected": selected,
        "fallback": fallback,
        "overlay_path": str(overlay_path.resolve()),
        "deployment_file_generated": False,
        "limits": [
            "本轮只验证 1920x1080 固定机位静态 ROI，不包含目标检测、跟踪或语义 ROI。",
            "隐私区必须遮挡或模糊，不能用高 QP 代替；本配置不包含隐私区。",
            "ROI 候选无严格正码率收益时回退无 ROI AQ2 对照。",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "roi_study.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "variant",
        "roi_enabled",
        "crf",
        "bitrate_mbps",
        "vmaf_mean",
        "vmaf_p5",
        "ssim",
        "encode_speed_x",
        "speed_tier",
        "speed_gate_enabled",
        "min_speed_x",
        "quality_pass",
        "speed_pass",
        "cache_hit",
        "output_path",
    ]
    with (output_dir / "roi_quality_points.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for variant, enabled, search in (
            ("control", False, control_search),
            ("roi", True, roi_search),
        ):
            for point in sorted(search.points, key=lambda item: item.crf):
                writer.writerow(
                    {
                        "variant": variant,
                        "roi_enabled": enabled,
                        "crf": f"{point.crf:.1f}",
                        "bitrate_mbps": f"{point.bitrate_bps / 1_000_000:.6f}",
                        "vmaf_mean": f"{point.vmaf_mean:.6f}",
                        "vmaf_p5": f"{point.vmaf_p5:.6f}",
                        "ssim": f"{point.ssim:.8f}",
                        "encode_speed_x": f"{point.encode_speed_x:.4f}",
                        "speed_tier": point.speed_tier,
                        "speed_gate_enabled": mode["speed_gate_enabled"],
                        "min_speed_x": mode["min_speed_x"],
                        "quality_pass": point.quality_pass,
                        "speed_pass": point.speed_pass,
                        "cache_hit": point.cache_hit,
                        "output_path": point.output_path,
                    }
                )

    region_fields = [
        "region_id",
        "title",
        "role",
        "x",
        "y",
        "width",
        "height",
        "control_vmaf",
        "roi_vmaf",
        "vmaf_drop",
        "control_vmaf_p5",
        "roi_vmaf_p5",
        "vmaf_p5_drop",
        "control_ssim",
        "roi_ssim",
        "ssim_drop",
        "quality_pass",
        "control_cache_hit",
        "roi_cache_hit",
    ]
    with (output_dir / "roi_region_quality.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=region_fields)
        writer.writeheader()
        for item in region_quality:
            region = item.region
            writer.writerow(
                {
                    "region_id": region.region_id,
                    "title": region.title,
                    "role": region.role,
                    "x": region.x,
                    "y": region.y,
                    "width": region.width,
                    "height": region.height,
                    "control_vmaf": f"{item.control.vmaf_mean:.6f}",
                    "roi_vmaf": f"{item.roi.vmaf_mean:.6f}",
                    "vmaf_drop": f"{item.vmaf_drop:.6f}",
                    "control_vmaf_p5": f"{item.control.vmaf_p5:.6f}",
                    "roi_vmaf_p5": f"{item.roi.vmaf_p5:.6f}",
                    "vmaf_p5_drop": f"{item.vmaf_p5_drop:.6f}",
                    "control_ssim": f"{item.control.ssim:.8f}",
                    "roi_ssim": f"{item.roi.ssim:.8f}",
                    "ssim_drop": f"{item.ssim_drop:.8f}",
                    "quality_pass": item.quality_pass,
                    "control_cache_hit": item.control.cache_hit,
                    "roi_cache_hit": item.roi.cache_hit,
                }
            )

    lines = [
        "# 固定机位静态 ROI 等画质研究",
        "",
        f"- 模式：{mode['title']}（`{mode['name']}`）",
        f"- 摄像头：`{settings.camera_id}` / {settings.reference_width}x{settings.reference_height}",
        f"- 配置哈希：`{settings.config_hash}`",
        f"- 全局画质门槛：VMAF≥{thresholds.vmaf_mean}，P5≥{thresholds.vmaf_p5}，SSIM≥{thresholds.ssim}",
        "- 编码共同条件：AQ2 / strength 1.0 / qg-size 32 / Main 8-bit",
        "",
        "## 全局等画质配对",
        "",
    ]
    if match.pair:
        pair = match.pair
        lines.extend(
            [
                f"- 无 ROI：CRF {pair.baseline.crf:.1f}，VMAF {pair.baseline.vmaf_mean:.3f}，"
                f"P5 {pair.baseline.vmaf_p5:.3f}，SSIM {pair.baseline.ssim:.6f}，"
                f"码率 {pair.baseline.bitrate_bps / 1_000_000:.3f} Mbit/s。",
                f"- ROI：CRF {pair.optimized.crf:.1f}，VMAF {pair.optimized.vmaf_mean:.3f}，"
                f"P5 {pair.optimized.vmaf_p5:.3f}，SSIM {pair.optimized.ssim:.6f}，"
                f"码率 {pair.optimized.bitrate_bps / 1_000_000:.3f} Mbit/s。",
                f"- |delta VMAF|={pair.vmaf_delta:.3f}，平均视频包码率收益 {pair.algorithm_saving_pct:.2f}%。",
            ]
        )
    else:
        lines.append(f"- 证据不足：{match.reason}。")
    lines.extend(["", "## 重点区域画质", ""])
    if region_quality:
        for item in region_quality:
            status = "通过" if item.quality_pass else "不通过"
            lines.append(
                f"- {item.region.title}（{item.region.role}）：VMAF 下降 {item.vmaf_drop:.3f}，"
                f"P5 下降 {item.vmaf_p5_drop:.3f}，SSIM 下降 {item.ssim_drop:.6f}，"
                f"{status}。"
            )
    else:
        lines.append("- 未找到全局等画质配对，未进入局部指标决策。")
    lines.extend(["", "## 决定", ""])
    if decision.selected:
        lines.append("ROI 候选通过全局、局部、速度和码率门槛，本研究选中 ROI。")
    else:
        lines.append("回退无 ROI AQ2 对照，不生成部署文件。")
        for reason in decision.reasons:
            lines.append(f"- {reason}。")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 本轮只是固定坐标 ROI，不识别或跟踪人员/车辆。",
            "- 隐私区必须单独遮挡或模糊，不能以高 QP 替代。",
            f"- ROI 可视化：`{overlay_path.name}`（红=critical，黄=evidence，蓝=discard）。",
            "",
        ]
    )
    (output_dir / "roi_study_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return payload
