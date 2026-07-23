import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.feasibility import ContinuityValidation, FeasibilityConclusions
from ..core.matching import EqualQualityMatchResult
from ..core.models import ComparisonPlan, EncoderConditions, ReferenceArtifact, VideoInfo
from ..core.search import QualitySearchResult, QualityThresholds


def write_pair_search_reports(
    output_dir: Path,
    source: VideoInfo,
    reference: ReferenceArtifact,
    plan: ComparisonPlan,
    conditions: EncoderConditions,
    thresholds: QualityThresholds,
    baseline_search: QualitySearchResult,
    optimized_search: QualitySearchResult,
    match: EqualQualityMatchResult,
    continuity: ContinuityValidation,
    conclusions: FeasibilityConclusions,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "study": "H.265 两方案独立 CRF 搜索与肉眼无损近似配对",
        "input": source.to_dict(),
        "reference": reference.to_dict(),
        "mode": plan.mode.to_dict(),
        "encoder_conditions": conditions.to_dict(),
        "quality_thresholds": thresholds.to_dict(),
        "configs": {
            "baseline": plan.baseline.to_dict(reference.video.fps),
            "optimized": plan.optimized.to_dict(reference.video.fps),
        },
        "searches": {
            "baseline": baseline_search.to_dict(),
            "optimized": optimized_search.to_dict(),
        },
        "match": match.to_dict(),
        "continuity_validation": continuity.to_dict(),
        "conclusions": conclusions.to_dict(),
        "limits": [
            "两路必须先分别满足所选模式的 VMAF、VMAF P5 和 SSIM 绝对门槛。",
            f"VMAF 差绝对值不超过 {match.max_vmaf_delta:.1f} 是本项目的肉眼无损近似标准，不表示像素无损。",
            "算法可行性只适用于本样本、当前 libx265 和实验参数。",
            (
                "画面连续性检查完整解码、规格、帧数、时长和时间戳；"
                + (
                    f"当前模式另有 {plan.mode.min_speed_x:.2f}x 速度门槛。"
                    if plan.mode.min_speed_x is not None
                    else "当前激进模式不设速度硬门槛，低于1.0x时标记为离线编码。"
                )
            ),
            "部署结论只是摄像头实机验证初筛，不代表已经完成硬件验证。",
        ],
    }
    (output_dir / "pair_search.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "scheme",
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
        "cache_hit",
        "output_path",
    ]
    with (output_dir / "pair_quality_points.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for scheme_name, search in (
            ("baseline", baseline_search),
            ("optimized", optimized_search),
        ):
            for point in sorted(search.points, key=lambda item: item.crf):
                writer.writerow(
                    {
                        "scheme": scheme_name,
                        "crf": f"{point.crf:.1f}",
                        "bitrate_mbps": f"{point.bitrate_bps / 1_000_000:.6f}",
                        "vmaf_mean": f"{point.vmaf_mean:.6f}",
                        "vmaf_p5": f"{point.vmaf_p5:.6f}",
                        "ssim": f"{point.ssim:.8f}",
                        "encode_speed_x": f"{point.encode_speed_x:.4f}",
                        "speed_tier": point.speed_tier,
                        "speed_gate_enabled": plan.mode.min_speed_x is not None,
                        "min_speed_x": plan.mode.min_speed_x,
                        "quality_pass": point.quality_pass,
                        "cache_hit": point.cache_hit,
                        "output_path": point.output_path,
                    }
                )

    baseline_selected = baseline_search.selected
    optimized_selected = optimized_search.selected
    lines = [
        "# 两方案独立 CRF 搜索与肉眼无损近似配对",
        "",
        f"- 模式：{plan.mode.title}（`{plan.mode.name}`）",
        f"- 绝对画质门槛：VMAF≥{thresholds.vmaf_mean}，P5≥{thresholds.vmaf_p5}，SSIM≥{thresholds.ssim}",
        f"- 配对门槛：两路 VMAF 差绝对值不超过 {match.max_vmaf_delta:.1f}",
        f"- 工程基线搜索点：{len(baseline_search.points)} 个",
        f"- 优化组合搜索点：{len(optimized_search.points)} 个",
        "",
        "## 各方案画质边界",
        "",
    ]
    if baseline_selected:
        lines.append(
            f"- 工程基线最高合格点：CRF {baseline_selected.crf:.1f}，"
            f"VMAF {baseline_selected.vmaf_mean:.3f}，"
            f"P5 {baseline_selected.vmaf_p5:.3f}，"
            f"码率 {baseline_selected.bitrate_bps / 1_000_000:.3f} Mbit/s。"
        )
    else:
        lines.append("- 工程基线在 CRF 18～38 内没有画质合格点。")
    if optimized_selected:
        lines.append(
            f"- 优化组合最高合格点：CRF {optimized_selected.crf:.1f}，"
            f"VMAF {optimized_selected.vmaf_mean:.3f}，"
            f"P5 {optimized_selected.vmaf_p5:.3f}，"
            f"码率 {optimized_selected.bitrate_bps / 1_000_000:.3f} Mbit/s。"
        )
    else:
        lines.append("- 优化组合在 CRF 18～38 内没有画质合格点。")
    lines.extend(["", "## 配对结果", ""])
    if match.pair:
        pair = match.pair
        lines.extend(
            [
                "**已找到肉眼无损近似配对。**",
                f"- 工程基线：CRF {pair.baseline.crf:.1f}，VMAF {pair.baseline.vmaf_mean:.3f}，"
                f"码率 {pair.baseline.bitrate_bps / 1_000_000:.3f} Mbit/s。",
                f"- 优化组合：CRF {pair.optimized.crf:.1f}，VMAF {pair.optimized.vmaf_mean:.3f}，"
                f"码率 {pair.optimized.bitrate_bps / 1_000_000:.3f} Mbit/s。",
                f"- |ΔVMAF|：{pair.vmaf_delta:.3f}。",
                f"- 配对点算法码率变化：{pair.algorithm_saving_pct:.2f}%。",
                "",
                "该百分比将按当前模式门槛进入三种独立结论。",
            ]
        )
    else:
        lines.extend(
            [
                "**证据不足。**",
                match.reason,
                "因此不得宣布优化成功。",
            ]
        )
    lines.extend(["", "## 三种独立结论", ""])
    for conclusion in (
        conclusions.algorithm,
        conclusions.software_continuity,
        conclusions.deployment,
    ):
        status = "通过" if conclusion.passed else "不通过"
        lines.extend(
            [
                f"### {conclusion.title}：{status}",
                "",
                f"- 决定：`{conclusion.decision}`",
                f"- 原因：{conclusion.reason}",
                f"- 适用边界：{conclusion.scope}",
                "",
            ]
        )
    lines.extend(
        [
            "",
            "## 配对范围",
            "",
            f"- 工程基线边界候选 CRF：{', '.join(f'{value:.1f}' for value in match.baseline_boundary_crfs) or '无'}",
            f"- 优化组合边界候选 CRF：{', '.join(f'{value:.1f}' for value in match.optimized_boundary_crfs) or '无'}",
            f"- 实际比较 {match.evaluated_pair_count} 对，其中 {match.qualifying_pair_count} 对通过差值门槛。",
            "",
        ]
    )
    (output_dir / "pair_search_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return payload
