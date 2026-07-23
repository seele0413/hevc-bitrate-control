import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.models import EncoderConditions, InterConfig, ReferenceArtifact, VideoInfo
from ..core.search import QualitySearchResult


def write_quality_search_reports(
    output_dir: Path,
    source: VideoInfo,
    reference: ReferenceArtifact,
    mode: dict,
    scheme: InterConfig,
    conditions: EncoderConditions,
    search: QualitySearchResult,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "study": "H.265 单方案自适应 CRF 画质搜索",
        "input": source.to_dict(),
        "reference": reference.to_dict(),
        "mode": mode,
        "scheme": scheme.to_dict(reference.video.fps),
        "encoder_conditions": conditions.to_dict(),
        "search": search.to_dict(),
        "limits": [
            "本报告只证明单个方案的画质边界搜索已经完成。",
            "工程基线与优化组合的等画质配对属于下一开发任务。",
            "最终入选点是满足全部画质门槛的最高 CRF，不代表已经满足部署条件。",
        ],
    }
    (output_dir / "search.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
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
    with (output_dir / "quality_points.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for point in sorted(search.points, key=lambda item: item.crf):
            writer.writerow(
                {
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

    thresholds = search.spec.thresholds
    selected = search.selected
    lines = [
        "# 单方案自适应 CRF 搜索摘要",
        "",
        f"- 模式：{mode['title']}（`{mode['name']}`）",
        f"- 方案：{scheme.title}（`{scheme.name}`）",
        f"- 范围：CRF {search.spec.crf_min:.1f}～{search.spec.crf_max:.1f}，步长 {search.spec.crf_step:.1f}",
        f"- 初始锚点：{', '.join(f'{value:.1f}' for value in search.spec.anchors)}",
        f"- 画质门槛：VMAF≥{thresholds.vmaf_mean}，P5≥{thresholds.vmaf_p5}，SSIM≥{thresholds.ssim}",
        f"- 实际测试：{len(search.points)} 个点，其中缓存命中 {sum(1 for point in search.points if point.cache_hit)} 个",
        f"- 非单调全网格补测：{'是' if search.exhaustive_fallback else '否'}",
        "",
        "## 搜索结论",
        "",
    ]
    if selected:
        lines.extend(
            [
                f"满足全部画质门槛的最高点是 **CRF {selected.crf:.1f}**。",
                f"该点码率 {selected.bitrate_bps / 1_000_000:.3f} Mbit/s，"
                f"VMAF {selected.vmaf_mean:.3f}，P5 {selected.vmaf_p5:.3f}，"
                f"SSIM {selected.ssim:.6f}。",
            ]
        )
    else:
        lines.append("CRF 18.0～38.0 内没有候选同时满足全部画质门槛。")
    if search.monotonicity_violations:
        lines.extend(["", "## 非单调证据", ""])
        lines.extend(f"- {item}" for item in search.monotonicity_violations)
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "本结果尚未与另一个 H.265 方案做等画质配对，不能据此宣布算法节码率成功。",
            "",
        ]
    )
    (output_dir / "search_summary.md").write_text("\n".join(lines), encoding="utf-8")
    return payload
