import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from ..core.aq import AdaptiveQuantizationTrial
from ..core.models import (
    AdaptiveQuantizationSettings,
    EncoderConditions,
    InterConfig,
    ReferenceArtifact,
    VideoInfo,
)
from ..core.search import QualitySearchResult, QualityThresholds


def write_aq_study_reports(
    output_dir: Path,
    source: VideoInfo,
    reference: ReferenceArtifact,
    mode: dict,
    scheme: InterConfig,
    conditions: EncoderConditions,
    thresholds: QualityThresholds,
    control_profile: AdaptiveQuantizationSettings,
    control_search: QualitySearchResult,
    trials: Iterable[AdaptiveQuantizationTrial],
    selected: Optional[AdaptiveQuantizationTrial],
) -> dict:
    trials = list(trials)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "study": "H.265 AQ 自适应量化等画质研究",
        "input": source.to_dict(),
        "reference": reference.to_dict(),
        "mode": mode,
        "scheme": scheme.to_dict(reference.video.fps),
        "encoder_conditions": conditions.to_dict(),
        "quality_thresholds": thresholds.to_dict(),
        "control": {
            "profile": control_profile.to_dict(),
            "search": control_search.to_dict(),
        },
        "trials": [trial.to_dict() for trial in trials],
        "selected": selected.to_dict() if selected else None,
        "decision": "aq_profile_selected" if selected else "default_aq_fallback",
        "limits": [
            "x265 内置 AQ 根据方差、暗场或边缘信息分配块级 QP，不识别人、机器人或文字语义。",
            "aq-motion 是实验功能，且相对运动越大时使用更多量化；本研究默认关闭，不能把它描述为运动区域保护。",
            "本阶段使用全局 VMAF、帧级 VMAF P5 和 SSIM；空间 ROI 画质图和语义 ROI 编码尚未实现。",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "aq_study.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "profile",
        "aq_mode",
        "aq_strength",
        "qg_size",
        "aq_motion",
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
    with (output_dir / "aq_quality_points.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        rows = [(control_profile, control_search)] + [
            (trial.profile, trial.search) for trial in trials
        ]
        for profile, search in rows:
            for point in sorted(search.points, key=lambda item: item.crf):
                writer.writerow(
                    {
                        "profile": profile.name,
                        "aq_mode": profile.aq_mode,
                        "aq_strength": f"{profile.aq_strength:.2f}",
                        "qg_size": profile.qg_size,
                        "aq_motion": profile.aq_motion,
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
                        "cache_hit": point.cache_hit,
                        "output_path": point.output_path,
                    }
                )

    lines = [
        "# AQ 自适应量化等画质研究",
        "",
        f"- 模式：{mode['title']}（`{mode['name']}`）",
        f"- 方案：{scheme.title}",
        f"- 画质门槛：VMAF≥{thresholds.vmaf_mean}，P5≥{thresholds.vmaf_p5}，SSIM≥{thresholds.ssim}",
        "- 对照：AQ2 / strength 1.0 / qg-size 32 / aq-motion 关闭",
        "",
        "## 画质边界",
        "",
    ]
    control = control_search.selected
    if control:
        lines.append(
            f"- 默认 AQ2：CRF {control.crf:.1f}，VMAF {control.vmaf_mean:.3f}，"
            f"P5 {control.vmaf_p5:.3f}，码率 {control.bitrate_bps / 1_000_000:.3f} Mbit/s。"
        )
    else:
        lines.append("- 默认 AQ2 在搜索范围内没有画质合格点。")
    for trial in trials:
        point = trial.search.selected
        profile = trial.profile
        if point:
            lines.append(
                f"- {profile.title}（AQ{profile.aq_mode} / strength {profile.aq_strength:.1f} / "
                f"qg-size {profile.qg_size}）：CRF {point.crf:.1f}，"
                f"VMAF {point.vmaf_mean:.3f}，P5 {point.vmaf_p5:.3f}，"
                f"码率 {point.bitrate_bps / 1_000_000:.3f} Mbit/s。"
            )
        else:
            lines.append(f"- {profile.title}在搜索范围内没有画质合格点。")

    lines.extend(["", "## 等画质配对", ""])
    for trial in trials:
        pair = trial.match.pair
        if pair:
            lines.append(
                f"- {trial.profile.title}：对照 CRF {pair.baseline.crf:.1f} / "
                f"候选 CRF {pair.optimized.crf:.1f}，|ΔVMAF|={pair.vmaf_delta:.3f}，"
                f"平均码率变化 {pair.algorithm_saving_pct:.2f}%，"
                f"{'可采用' if trial.bitrate_beneficial else '不采用'}。"
            )
        else:
            lines.append(f"- {trial.profile.title}：证据不足，{trial.match.reason}。")

    lines.extend(["", "## 决定", ""])
    if selected and selected.match.pair:
        pair = selected.match.pair
        lines.extend(
            [
                f"选择 **{selected.profile.title}**。",
                f"等画质配对平均码率降低 {pair.algorithm_saving_pct:.2f}%，"
                f"|ΔVMAF|={pair.vmaf_delta:.3f}。",
            ]
        )
    else:
        lines.append("没有候选在等画质条件下带来正向码率收益，回退默认 AQ2。")
    lines.extend(
        [
            "",
            "## 当前边界",
            "",
            "- 内置 AQ 不是语义识别，不能声称已经识别人、机器人或文字。",
            "- 暗场 AQ3 和边缘 AQ4 不能叠加，本轮将它们作为独立候选。",
            "- 运动 AQ 仍为实验功能且方向不等同于运动区域保护，本轮关闭。",
            "- 空间 ROI 质量图和语义 ROI 编码尚未实现。",
            "",
        ]
    )
    (output_dir / "aq_study_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return payload
