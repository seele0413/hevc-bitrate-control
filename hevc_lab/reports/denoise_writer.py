import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..core.denoise import DenoiseStudyDecision
from ..core.matching import EqualQualityMatchResult
from ..core.models import (
    AdaptiveQuantizationSettings,
    DenoiseSettings,
    EncoderConditions,
    InterConfig,
    ReferenceArtifact,
    VideoInfo,
)
from ..core.roi import ROIRegionQuality
from ..core.search import QualitySearchResult, QualityThresholds


def write_denoise_study_reports(
    output_dir: Path,
    source: VideoInfo,
    reference: ReferenceArtifact,
    mode: dict,
    scheme: InterConfig,
    conditions: EncoderConditions,
    thresholds: QualityThresholds,
    aq_profile: AdaptiveQuantizationSettings,
    denoise_settings: DenoiseSettings,
    control_search: QualitySearchResult,
    denoise_search: QualitySearchResult,
    match: EqualQualityMatchResult,
    region_quality: Iterable[ROIRegionQuality],
    decision: DenoiseStudyDecision,
    overlay_path: Path,
) -> dict:
    region_quality = tuple(region_quality)
    selected = match.pair.optimized.to_dict() if decision.selected and match.pair else None
    fallback = match.pair.baseline.to_dict() if not decision.selected and match.pair else None
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "study": "H.265 ROI 保护的无效噪声抑制研究",
        "input": source.to_dict(),
        "reference": reference.to_dict(),
        "mode": mode,
        "scheme": scheme.to_dict(reference.video.fps),
        "encoder_conditions": conditions.to_dict(),
        "adaptive_quantization": aq_profile.to_dict(),
        "quality_thresholds": thresholds.to_dict(),
        "denoise_settings": denoise_settings.to_dict(),
        "control_search": control_search.to_dict(),
        "denoise_search": denoise_search.to_dict(),
        "match": match.to_dict(),
        "region_quality": [item.to_dict() for item in region_quality],
        "decision": decision.to_dict(),
        "selected": selected,
        "fallback": fallback,
        "overlay_path": str(overlay_path.resolve()),
        "deployment_file_generated": False,
        "limits": [
            "当前只研究 hqdn3d 随机空域/时域噪声抑制，不代表已解决雨雪、烟雾或光线闪烁。",
            "evidence 区域使用原图直通；critical 区域只做轻度降噪并单独复算画质。",
            "降噪候选无严格正码率收益时回退无降噪 AQ2 对照。",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "denoise_study.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "variant",
        "denoise_enabled",
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
    with (output_dir / "denoise_quality_points.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for variant, enabled, search in (
            ("control", False, control_search),
            ("denoise", True, denoise_search),
        ):
            for point in sorted(search.points, key=lambda item: item.crf):
                writer.writerow(
                    {
                        "variant": variant,
                        "denoise_enabled": enabled,
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
        "control_vmaf",
        "denoise_vmaf",
        "vmaf_drop",
        "control_vmaf_p5",
        "denoise_vmaf_p5",
        "vmaf_p5_drop",
        "control_ssim",
        "denoise_ssim",
        "ssim_drop",
        "quality_pass",
        "control_cache_hit",
        "denoise_cache_hit",
    ]
    with (output_dir / "denoise_region_quality.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=region_fields)
        writer.writeheader()
        for item in region_quality:
            writer.writerow(
                {
                    "region_id": item.region.region_id,
                    "title": item.region.title,
                    "role": item.region.role,
                    "control_vmaf": f"{item.control.vmaf_mean:.6f}",
                    "denoise_vmaf": f"{item.roi.vmaf_mean:.6f}",
                    "vmaf_drop": f"{item.vmaf_drop:.6f}",
                    "control_vmaf_p5": f"{item.control.vmaf_p5:.6f}",
                    "denoise_vmaf_p5": f"{item.roi.vmaf_p5:.6f}",
                    "vmaf_p5_drop": f"{item.vmaf_p5_drop:.6f}",
                    "control_ssim": f"{item.control.ssim:.8f}",
                    "denoise_ssim": f"{item.roi.ssim:.8f}",
                    "ssim_drop": f"{item.ssim_drop:.8f}",
                    "quality_pass": item.quality_pass,
                    "control_cache_hit": item.control.cache_hit,
                    "denoise_cache_hit": item.roi.cache_hit,
                }
            )

    lines = [
        "# ROI 保护的无效噪声抑制研究",
        "",
        f"- 模式：{mode['title']}（`{mode['name']}`）",
        f"- 摄像头：`{denoise_settings.roi.camera_id}` / "
        f"{denoise_settings.roi.reference_width}x{denoise_settings.roi.reference_height}",
        f"- 全局画质门槛：VMAF≥{thresholds.vmaf_mean}，P5≥{thresholds.vmaf_p5}，SSIM≥{thresholds.ssim}",
        "- 编码共同条件：AQ2 / strength 1.0 / qg-size 32 / Main 8-bit",
        "",
        "## 区域降噪策略",
        "",
    ]
    for role in ("critical", "evidence", "normal", "discard"):
        strength = denoise_settings.policy.strength_for(role)
        lines.append(f"- {role}：`{strength.filter_expression()}`")
    lines.extend(["", "## 全局等画质配对", ""])
    if match.pair:
        pair = match.pair
        lines.extend(
            [
                f"- 无降噪：CRF {pair.baseline.crf:.1f}，VMAF {pair.baseline.vmaf_mean:.3f}，"
                f"P5 {pair.baseline.vmaf_p5:.3f}，SSIM {pair.baseline.ssim:.6f}，"
                f"码率 {pair.baseline.bitrate_bps / 1_000_000:.3f} Mbit/s。",
                f"- 降噪：CRF {pair.optimized.crf:.1f}，VMAF {pair.optimized.vmaf_mean:.3f}，"
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
        lines.append("降噪候选通过全局、局部、速度和码率门槛，本研究选中降噪。")
    else:
        lines.append("回退无降噪 AQ2 对照，不生成部署文件。")
        for reason in decision.reasons:
            lines.append(f"- {reason}。")
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 当前只处理随机空域/时域噪声，不代表雨雪、烟雾、闪烁或振铃已解决。",
            "- 增益、曝光和过度锐化应优先在摄像头侧控制。",
            f"- 区域可视化：`{overlay_path.name}`。",
            "",
        ]
    )
    (output_dir / "denoise_study_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return payload
