import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..core.models import CandidateResult, InterConfig, ReferenceArtifact, VideoInfo


def _mbps(value: float) -> float:
    return value / 1_000_000.0


def write_reports(
    output_dir: Path,
    source: VideoInfo,
    settings: dict,
    configs: Iterable[InterConfig],
    candidates: list,
    best_candidate: Optional[CandidateResult],
    selected: Optional[CandidateResult],
    selected_output: Optional[Path],
    reference: Optional[ReferenceArtifact] = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_fps = reference.video.fps if reference else source.fps
    config_map = {config.name: config.to_dict(comparison_fps) for config in configs}
    payload = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "study": "H.265 帧间预测效率",
        "input": source.to_dict(),
        "reference": reference.to_dict() if reference else None,
        "settings": settings,
        "mode": settings.get("mode"),
        "configs": config_map,
        "candidates": [candidate.to_dict() for candidate in candidates],
        "best_interframe_candidate": best_candidate.to_dict() if best_candidate else None,
        "deployment_decision": "encode" if selected else "passthrough",
        "selected": selected.to_dict() if selected else None,
        "selected_output": str(selected_output) if selected_output else None,
        "limits": [
            "当前仅测试视频流，不包含音频。",
            "固定 CRF 实验用于筛选帧间预测组合，不等同于最终等画质 CRF 搜索。",
            "相对源流码率仅供参考；若源编码器或编码参数不同，不应直接归因于帧间预测参数。",
        ],
    }
    json_path = output_dir / "experiment.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = output_dir / "comparison.csv"
    fields = [
        "mode",
        "name",
        "title",
        "crf",
        "preset",
        "bitrate_mbps",
        "saving_vs_source_pct",
        "saving_vs_baseline_pct",
        "vmaf_mean",
        "vmaf_p5",
        "ssim",
        "encode_speed_x",
        "speed_tier",
        "quality_pass",
        "speed_pass",
        "eligible",
        "x265_params",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in candidates:
            writer.writerow(
                {
                    "mode": settings.get("mode", {}).get("name", "balanced"),
                    "name": item.name,
                    "title": item.title,
                    "crf": item.crf,
                    "preset": item.preset,
                    "bitrate_mbps": f"{_mbps(item.bitrate_bps):.6f}",
                    "saving_vs_source_pct": f"{item.bitrate_saving_vs_source_pct:.3f}",
                    "saving_vs_baseline_pct": f"{item.bitrate_saving_vs_baseline_pct:.3f}",
                    "vmaf_mean": f"{item.vmaf_mean:.4f}",
                    "vmaf_p5": f"{item.vmaf_p5:.4f}",
                    "ssim": f"{item.ssim:.8f}",
                    "encode_speed_x": f"{item.encode_speed_x:.4f}",
                    "speed_tier": item.speed_tier,
                    "quality_pass": item.quality_pass,
                    "speed_pass": item.speed_pass,
                    "eligible": item.eligible,
                    "x265_params": item.x265_params,
                }
            )

    summary_path = output_dir / "summary.md"
    speed_rule = (
        "不设硬门槛（低于1.0x时标记为离线编码）"
        if settings["min_speed_x"] is None
        else f"≥{settings['min_speed_x']:.2f}x"
    )
    lines = [
        "# H.265 帧间预测效率实验摘要",
        "",
        "## 实验原则",
        "",
        f"- 运行模式：{settings.get('mode', {}).get('title', '综合模式')}（`{settings.get('mode', {}).get('name', 'balanced')}`）",
        f"- 取舍优先级：{settings.get('mode', {}).get('priority', 'balanced')}",
        f"本轮固定 CRF={settings['crf']}、preset={settings['preset']}，两路只改变参考帧、B 帧、前向分析和 GOP；scenecut、CU-Tree 与加权预测保持一致。",
        "程序不会通过手动压低目标码率制造节省结果；候选按所选模式的画质和速度策略判断。",
        "",
        "## 输入与门槛",
        "",
        f"- 输入：`{source.path}`",
        f"- 输入编码：{source.codec}，{source.width}×{source.height}，{source.fps:.3f} fps",
        f"- 输入视频码率：{_mbps(source.video_bitrate_bps):.3f} Mbit/s",
        f"- 画质门槛：VMAF≥{settings['target_vmaf']}，VMAF P5≥{settings['target_vmaf_p5']}，SSIM≥{settings['target_ssim']}",
        f"- 速度门槛：{speed_rule}",
        f"- 算法节省门槛：相对工程基线≥{settings.get('min_algorithm_saving_pct', 0.0):.2f}%",
        f"- 源流节省门槛：≥{settings['min_source_saving_pct']:.2f}%",
        "",
        "## 候选结果",
        "",
        "| 候选 | 码率 Mbit/s | 较源流节省 | 较基线节省 | VMAF | VMAF P5 | SSIM | 速度 | 合格 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    if reference:
        lines[lines.index("## 候选结果"):lines.index("## 候选结果")] = [
            "## 统一参考片段",
            "",
            f"- 无损参考：`{reference.path}`",
            f"- 片段：{reference.requested_start_seconds:.3f}s 起，"
            f"实际 {reference.effective_duration_seconds:.3f}s",
            f"- 帧数：{reference.frame_count}（预期约 {reference.expected_frame_count}）",
            f"- 输入 SHA256：`{reference.input_sha256}`",
            f"- 缓存：{'命中' if reference.cache_hit else '新建'}",
            "",
        ]
    for item in candidates:
        lines.append(
            f"| {item.title} | {_mbps(item.bitrate_bps):.3f} | "
            f"{item.bitrate_saving_vs_source_pct:.2f}% | "
            f"{item.bitrate_saving_vs_baseline_pct:.2f}% | {item.vmaf_mean:.3f} | "
            f"{item.vmaf_p5:.3f} | {item.ssim:.6f} | {item.encode_speed_x:.2f}x | "
            f"{'是' if item.eligible else '否'} |"
        )
    lines.extend(["", "## 参数研究结论", ""])
    if best_candidate:
        lines.append(
            f"最佳帧间参数候选为 **{best_candidate.title}**，相对同条件工程基线节省 "
            f"**{best_candidate.bitrate_saving_vs_baseline_pct:.2f}%**。"
        )
    else:
        lines.append("没有候选同时通过画质和速度门槛，当前帧间参数实验无有效结果。")
    lines.extend(["", "## 部署结论", ""])
    if selected:
        lines.extend(
            [
                f"本轮选中 **{selected.title}**。在通过画质与速度门槛的候选中，它的码率最低。",
                f"相对工程基线码率变化为 **{selected.bitrate_saving_vs_baseline_pct:.2f}%**，"
                f"VMAF={selected.vmaf_mean:.3f}，VMAF P5={selected.vmaf_p5:.3f}，SSIM={selected.ssim:.6f}。",
                f"输出视频：`{selected_output}`",
            ]
        )
    else:
        if best_candidate:
            failure_reasons = []
            algorithm_saving = best_candidate.bitrate_saving_vs_baseline_pct
            min_algorithm_saving = settings.get("min_algorithm_saving_pct", 0.0)
            if algorithm_saving <= 0:
                failure_reasons.append(
                    f"相对工程基线未取得正收益（{algorithm_saving:.2f}%）"
                )
            elif algorithm_saving < min_algorithm_saving:
                failure_reasons.append(
                    f"相对工程基线节省 {algorithm_saving:.2f}%，"
                    f"未达到 {min_algorithm_saving:.2f}%"
                )
            source_saving = best_candidate.bitrate_saving_vs_source_pct
            if source_saving < settings["min_source_saving_pct"]:
                failure_reasons.append(
                    f"相对源流节省 {source_saving:.2f}%，"
                    f"未达到 {settings['min_source_saving_pct']:.2f}%"
                )
            reason_text = "；".join(failure_reasons) or "未通过当前模式的部署门槛"
            lines.append(
                f"**保持原码流直通。** {reason_text}，因此不生成可部署优化视频。"
            )
        else:
            lines.append("**保持原码流直通。** 没有候选通过画质与速度门槛。")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "相对工程基线的变化才用于判断帧间预测参数是否有效。相对源视频的变化会受到源编码器、源 CRF/码率控制方式等因素影响，仅作为背景数据。",
            "下一阶段应对有效参数组合执行等画质 CRF 搜索，并在真实固定机位监控录像上重复验证。",
            "",
        ]
    )
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return payload
