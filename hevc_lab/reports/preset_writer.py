import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.matching import EqualQualityMatchResult
from ..core.models import EncoderConditions, InterConfig, ReferenceArtifact, VideoInfo
from ..core.preset import PresetStudyDecision
from ..core.search import QualitySearchResult, QualityThresholds


def write_preset_study_reports(
    output_dir: Path,
    source: VideoInfo,
    reference: ReferenceArtifact,
    mode: dict,
    scheme: InterConfig,
    medium_conditions: EncoderConditions,
    slow_conditions: EncoderConditions,
    thresholds: QualityThresholds,
    medium_search: QualitySearchResult,
    slow_search: QualitySearchResult,
    match: EqualQualityMatchResult,
    decision: PresetStudyDecision,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "study": "x265 medium 与 slow 等画质压缩效率研究",
        "input": source.to_dict(),
        "reference": reference.to_dict(),
        "mode": mode,
        "scheme": scheme.to_dict(reference.video.fps),
        "quality_thresholds": thresholds.to_dict(),
        "speed_policy": {
            "speed_gate_enabled": mode["speed_gate_enabled"],
            "min_speed_x": mode["min_speed_x"],
            "speed_is_informational_for_aggressive": mode["name"] == "aggressive",
        },
        "presets": {
            "medium": {
                "role": "control",
                "encoder_conditions": medium_conditions.to_dict(),
                "search": medium_search.to_dict(),
            },
            "slow": {
                "role": "candidate",
                "encoder_conditions": slow_conditions.to_dict(),
                "search": slow_search.to_dict(),
            },
        },
        "match": match.to_dict(),
        "decision": decision.to_dict(),
        "mode_configuration_kept": mode["preset"],
        "limits": [
            "medium 与 slow 分别搜索 CRF，不能用相同 CRF 的码率差证明 preset 收益。",
            "本研究固定同一帧间参数、AQ、profile、像素格式和输入参考，只改变 preset。",
            "激进模式不使用速度淘汰候选；低于1.0x时只适合作为离线编码方案。",
            "preset 研究与正式帧间参数算法收益是两个独立结论。",
        ],
    }
    (output_dir / "preset_study.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "preset",
        "role",
        "crf",
        "bitrate_mbps",
        "vmaf_mean",
        "vmaf_p5",
        "ssim",
        "encode_seconds",
        "encode_speed_x",
        "speed_tier",
        "speed_gate_enabled",
        "min_speed_x",
        "quality_pass",
        "speed_pass",
        "eligible",
        "cache_hit",
        "output_path",
    ]
    with (output_dir / "preset_quality_points.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for preset, role, search in (
            ("medium", "control", medium_search),
            ("slow", "candidate", slow_search),
        ):
            for point in sorted(search.points, key=lambda item: item.crf):
                writer.writerow(
                    {
                        "preset": preset,
                        "role": role,
                        "crf": f"{point.crf:.1f}",
                        "bitrate_mbps": f"{point.bitrate_bps / 1_000_000:.6f}",
                        "vmaf_mean": f"{point.vmaf_mean:.6f}",
                        "vmaf_p5": f"{point.vmaf_p5:.6f}",
                        "ssim": f"{point.ssim:.8f}",
                        "encode_seconds": f"{point.encode_seconds:.4f}",
                        "encode_speed_x": f"{point.encode_speed_x:.4f}",
                        "speed_tier": point.speed_tier,
                        "speed_gate_enabled": mode["speed_gate_enabled"],
                        "min_speed_x": mode["min_speed_x"],
                        "quality_pass": point.quality_pass,
                        "speed_pass": point.speed_pass,
                        "eligible": point.eligible,
                        "cache_hit": point.cache_hit,
                        "output_path": point.output_path,
                    }
                )

    pair_fields = [
        "status",
        "medium_crf",
        "slow_crf",
        "medium_bitrate_mbps",
        "slow_bitrate_mbps",
        "vmaf_delta",
        "vmaf_p5_delta",
        "ssim_delta",
        "slow_saving_vs_medium_pct",
        "medium_encode_speed_x",
        "slow_encode_speed_x",
        "slow_speed_tier",
        "benefit_confirmed",
        "reason",
    ]
    with (output_dir / "preset_pair.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=pair_fields)
        writer.writeheader()
        pair = match.pair
        writer.writerow(
            {
                "status": decision.status,
                "medium_crf": f"{pair.baseline.crf:.1f}" if pair else "",
                "slow_crf": f"{pair.optimized.crf:.1f}" if pair else "",
                "medium_bitrate_mbps": (
                    f"{pair.baseline.bitrate_bps / 1_000_000:.6f}" if pair else ""
                ),
                "slow_bitrate_mbps": (
                    f"{pair.optimized.bitrate_bps / 1_000_000:.6f}" if pair else ""
                ),
                "vmaf_delta": f"{pair.vmaf_delta:.6f}" if pair else "",
                "vmaf_p5_delta": f"{pair.vmaf_p5_delta:.6f}" if pair else "",
                "ssim_delta": f"{pair.ssim_delta:.8f}" if pair else "",
                "slow_saving_vs_medium_pct": (
                    f"{pair.algorithm_saving_pct:.6f}" if pair else ""
                ),
                "medium_encode_speed_x": (
                    f"{pair.baseline.encode_speed_x:.4f}" if pair else ""
                ),
                "slow_encode_speed_x": (
                    f"{pair.optimized.encode_speed_x:.4f}" if pair else ""
                ),
                "slow_speed_tier": pair.optimized.speed_tier if pair else "",
                "benefit_confirmed": decision.benefit_confirmed,
                "reason": decision.reason,
            }
        )

    speed_rule = (
        "不设硬门槛，速度仅作信息记录"
        if mode["min_speed_x"] is None
        else f"不低于 {mode['min_speed_x']:.2f}x"
    )
    medium_selected = medium_search.selected
    slow_selected = slow_search.selected
    lines = [
        "# x265 medium 与 slow 等画质研究",
        "",
        f"- 模式：{mode['title']}（`{mode['name']}`）",
        f"- 固定方案：{scheme.title}",
        f"- 模式正式 preset：`{mode['preset']}`",
        f"- 速度规则：{speed_rule}",
        f"- 画质门槛：VMAF≥{thresholds.vmaf_mean}，P5≥{thresholds.vmaf_p5}，SSIM≥{thresholds.ssim}",
        f"- 配对门槛：|ΔVMAF|≤{match.max_vmaf_delta:.1f}",
        "",
        "## 画质边界",
        "",
    ]
    for name, point in (("medium", medium_selected), ("slow", slow_selected)):
        if point:
            lines.append(
                f"- `{name}`：CRF {point.crf:.1f}，VMAF {point.vmaf_mean:.3f}，"
                f"P5 {point.vmaf_p5:.3f}，SSIM {point.ssim:.6f}，"
                f"码率 {point.bitrate_bps / 1_000_000:.3f} Mbit/s，"
                f"速度 {point.encode_speed_x:.3f}x（`{point.speed_tier}`）。"
            )
        else:
            lines.append(f"- `{name}`：搜索范围内没有符合当前模式资格的边界点。")
    lines.extend(["", "## 等画质配对", ""])
    if match.pair:
        pair = match.pair
        lines.extend(
            [
                f"- medium：CRF {pair.baseline.crf:.1f}，码率 {pair.baseline.bitrate_bps / 1_000_000:.3f} Mbit/s。",
                f"- slow：CRF {pair.optimized.crf:.1f}，码率 {pair.optimized.bitrate_bps / 1_000_000:.3f} Mbit/s。",
                f"- |ΔVMAF|={pair.vmaf_delta:.3f}。",
                f"- slow 相对 medium 平均视频包码率变化：{pair.algorithm_saving_pct:.2f}%。",
                f"- slow 编码速度：{pair.optimized.encode_speed_x:.3f}x（`{pair.optimized.speed_tier}`）。",
            ]
        )
    else:
        lines.append(f"- 证据不足：{match.reason}。")
    lines.extend(
        [
            "",
            "## 结论",
            "",
            decision.reason + "。",
            f"激进模式仍按既定方案使用 `{mode['preset']}`；preset 收益与帧间参数收益分别报告。",
            "",
        ]
    )
    (output_dir / "preset_study_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return payload
