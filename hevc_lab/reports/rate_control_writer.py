import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..core.models import (
    CandidateResult,
    EncoderConditions,
    InterConfig,
    PacketBitrateStats,
    ReferenceArtifact,
    VideoInfo,
)
from ..core.rate_control import RateControlTrial
from ..core.search import QualitySearchResult, QualityThresholds


def write_rate_control_reports(
    output_dir: Path,
    source: VideoInfo,
    reference: ReferenceArtifact,
    mode: dict,
    scheme: InterConfig,
    conditions: EncoderConditions,
    thresholds: QualityThresholds,
    search: QualitySearchResult,
    uncapped: Optional[CandidateResult],
    uncapped_stats: Optional[PacketBitrateStats],
    trials: List[RateControlTrial],
    selected: Optional[RateControlTrial],
) -> dict:
    trial_payloads = (
        [trial.to_dict(uncapped_stats) for trial in trials]
        if uncapped_stats
        else []
    )
    selected_index = trials.index(selected) if selected in trials else None
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "study": "H.265 质量驱动 Capped CRF / VBV 实验",
        "input": source.to_dict(),
        "reference": reference.to_dict(),
        "mode": mode,
        "scheme": scheme.to_dict(reference.video.fps),
        "encoder_conditions": conditions.to_dict(),
        "quality_thresholds": thresholds.to_dict(),
        "uncapped_search": search.to_dict(),
        "uncapped": (
            {
                "candidate": uncapped.to_dict(),
                "packet_bitrate": uncapped_stats.to_dict(),
            }
            if uncapped and uncapped_stats
            else None
        ),
        "trials": trial_payloads,
        "selected_trial_index": selected_index,
        "selected": trial_payloads[selected_index] if selected_index is not None else None,
        "decision": "capped_crf_quality_preserved" if selected else "uncapped_fallback",
        "limits": [
            "本阶段使用 CRF 维持质量，VBV 只保护局部码率峰值，不把固定低码率当成算法。",
            "长期跨片段平均码率反馈控制尚未实现。",
            "VBV 结果必须同时通过模式绝对画质门槛和 VMAF 变化门槛。",
        ],
    }
    (output_dir / "rate_control.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "kind",
        "peak_ratio",
        "vbv_maxrate_kbps",
        "vbv_bufsize_kbits",
        "crf",
        "average_mbps",
        "peak_1s_mbps",
        "p95_1s_mbps",
        "vmaf_mean",
        "vmaf_p5",
        "ssim",
        "quality_preserved",
        "cache_hit",
    ]
    with (output_dir / "rate_control_points.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        if uncapped and uncapped_stats:
            writer.writerow(
                {
                    "kind": "uncapped_crf",
                    "peak_ratio": "",
                    "vbv_maxrate_kbps": "",
                    "vbv_bufsize_kbits": "",
                    "crf": f"{uncapped.crf:.1f}",
                    "average_mbps": f"{uncapped_stats.average_bitrate_bps / 1_000_000:.6f}",
                    "peak_1s_mbps": f"{uncapped_stats.peak_window_bitrate_bps / 1_000_000:.6f}",
                    "p95_1s_mbps": f"{uncapped_stats.p95_window_bitrate_bps / 1_000_000:.6f}",
                    "vmaf_mean": f"{uncapped.vmaf_mean:.6f}",
                    "vmaf_p5": f"{uncapped.vmaf_p5:.6f}",
                    "ssim": f"{uncapped.ssim:.8f}",
                    "quality_preserved": True,
                    "cache_hit": uncapped.cache_hit,
                }
            )
        for trial in trials:
            writer.writerow(
                {
                    "kind": "capped_crf",
                    "peak_ratio": f"{trial.peak_ratio:.2f}",
                    "vbv_maxrate_kbps": trial.settings.vbv_maxrate_kbps,
                    "vbv_bufsize_kbits": trial.settings.vbv_bufsize_kbits,
                    "crf": f"{trial.candidate.crf:.1f}",
                    "average_mbps": f"{trial.packet_stats.average_bitrate_bps / 1_000_000:.6f}",
                    "peak_1s_mbps": f"{trial.packet_stats.peak_window_bitrate_bps / 1_000_000:.6f}",
                    "p95_1s_mbps": f"{trial.packet_stats.p95_window_bitrate_bps / 1_000_000:.6f}",
                    "vmaf_mean": f"{trial.candidate.vmaf_mean:.6f}",
                    "vmaf_p5": f"{trial.candidate.vmaf_p5:.6f}",
                    "ssim": f"{trial.candidate.ssim:.8f}",
                    "quality_preserved": trial.quality_preserved,
                    "cache_hit": trial.candidate.cache_hit,
                }
            )

    lines = [
        "# 质量驱动 Capped CRF / VBV 实验",
        "",
        f"- 模式：{mode['title']}（`{mode['name']}`）",
        f"- 方案：{scheme.title}",
        f"- 画质门槛：VMAF≥{thresholds.vmaf_mean}，P5≥{thresholds.vmaf_p5}，SSIM≥{thresholds.ssim}",
        "- 码率口径：仅视频流压缩包字节；峰值按1秒时间窗统计。",
        "",
        "## 结果",
        "",
    ]
    if not uncapped or not uncapped_stats:
        lines.append("CRF 18～38 内没有画质合格点，未进行 VBV 实验。")
    else:
        lines.extend(
            [
                f"无上限 CRF 基准：CRF {uncapped.crf:.1f}，平均 "
                f"{uncapped_stats.average_bitrate_bps / 1_000_000:.3f} Mbit/s，"
                f"1秒峰值 {uncapped_stats.peak_window_bitrate_bps / 1_000_000:.3f} Mbit/s。",
                "",
            ]
        )
        for trial in trials:
            data = trial.to_dict(uncapped_stats)
            lines.append(
                f"- 峰值倍率 {trial.peak_ratio:.2f}：maxrate={trial.settings.vbv_maxrate_kbps} kbit/s，"
                f"平均变化 {data['average_saving_vs_uncapped_pct']:.2f}%，"
                f"1秒峰值变化 {data['peak_saving_vs_uncapped_pct']:.2f}%，"
                f"|ΔVMAF|={trial.vmaf_delta:.3f}，"
                f"画质{'通过' if trial.quality_preserved else '未通过'}，"
                f"码率{'改善' if trial.bitrate_beneficial else '未改善'}。"
            )
        lines.extend(["", "## 决定", ""])
        if selected:
            lines.append(
                f"选择峰值倍率 {selected.peak_ratio:.2f} 的 Capped CRF 设置；"
                "它是本轮从严格到宽松测试中首个保持画质的方案。"
            )
        else:
            lines.append("所有测试上限都未能同时保持画质并改善码率，回退到无上限 CRF。")
    lines.extend(
        [
            "",
            "本阶段只验证单片段质量和局部峰值。跨多个时间片的长期平均码率反馈尚未实现。",
            "",
        ]
    )
    (output_dir / "rate_control_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return payload
