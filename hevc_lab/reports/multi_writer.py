import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def _format_bitrate_mbps(value):
    return "" if value is None else f"{value / 1_000_000.0:.6f}"


def _format_saving(value):
    return "" if value is None else f"{value:.2f}"


def _format_bool(value):
    if value is None:
        return ""
    return "是" if value else "否"


def _format_metric(value, digits=3):
    return "" if value is None else f"{value:.{digits}f}"


def _format_markdown_metric(value, digits=3):
    return "—" if value is None else f"{value:.{digits}f}"


def write_multi_encode_reports(output_dir: Path, payload: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        **payload,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    manifest_path = output_dir / "research_manifest.json"
    manifest_temp = output_dir / "research_manifest.part.json"
    manifest_temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_temp.replace(manifest_path)

    fields = [
        "strategy",
        "resolution",
        "average_video_packet_bitrate_bps",
        "average_video_packet_bitrate_mbps",
        "selected_crf",
        "vmaf_mean",
        "vmaf_p5",
        "ssim",
        "encode_speed_x",
        "saving_vs_default_pct",
        "saving_vs_general_no_roi_pct",
        "budget_neutral_pass",
        "roi_quality_preserved",
        "roi_quality_improved",
    ]
    csv_path = output_dir / "final_metrics.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for strategy in payload["strategies"]:
            bitrate = strategy.get("average_video_packet_bitrate_bps")
            writer.writerow(
                {
                    "strategy": strategy["title"],
                    "resolution": strategy.get("resolution") or "",
                    "average_video_packet_bitrate_bps": (
                        "" if bitrate is None else f"{bitrate:.6f}"
                    ),
                    "average_video_packet_bitrate_mbps": _format_bitrate_mbps(
                        bitrate
                    ),
                    "selected_crf": _format_metric(
                        strategy.get("selected_crf"),
                        1,
                    ),
                    "vmaf_mean": _format_metric(strategy.get("vmaf_mean"), 3),
                    "vmaf_p5": _format_metric(strategy.get("vmaf_p5"), 3),
                    "ssim": _format_metric(strategy.get("ssim"), 6),
                    "encode_speed_x": _format_metric(
                        strategy.get("encode_speed_x"),
                        3,
                    ),
                    "saving_vs_default_pct": _format_saving(
                        strategy.get("saving_vs_default_pct")
                    ),
                    "saving_vs_general_no_roi_pct": _format_saving(
                        strategy.get("saving_vs_general_no_roi_pct")
                    ),
                    "budget_neutral_pass": _format_bool(
                        strategy.get("budget_neutral_pass")
                    ),
                    "roi_quality_preserved": _format_bool(
                        strategy.get("roi_quality_preserved")
                    ),
                    "roi_quality_improved": _format_bool(
                        strategy.get("roi_quality_improved")
                    ),
                }
            )

    lines = [
        "# x265默认编码与 V1.4 预算中性 ROI 最终数据",
        "",
        "| 编码策略 | 输出分辨率 | 平均视频包码率 | CRF | VMAF | P5 | SSIM | 编码速度 | 相对默认节省 | 相对通用节省 | 预算中性 | ROI重点区域保持 | ROI重点区域改善 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|:---:|",
    ]
    for strategy in payload["strategies"]:
        bitrate = strategy.get("average_video_packet_bitrate_bps")
        if bitrate is None:
            bitrate_text = "未生成"
        else:
            bitrate_text = f"{bitrate / 1_000_000.0:.6f} Mbit/s"
        saving = strategy.get("saving_vs_default_pct")
        saving_text = "—" if saving is None else f"{saving:.2f}%"
        general_saving = strategy.get("saving_vs_general_no_roi_pct")
        general_saving_text = "—" if general_saving is None else f"{general_saving:.2f}%"
        crf_text = _format_markdown_metric(strategy.get("selected_crf"), 1)
        vmaf_text = _format_markdown_metric(strategy.get("vmaf_mean"), 3)
        p5_text = _format_markdown_metric(strategy.get("vmaf_p5"), 3)
        ssim_text = _format_markdown_metric(strategy.get("ssim"), 6)
        speed = strategy.get("encode_speed_x")
        speed_text = "—" if speed is None else f"{speed:.3f}x"
        resolution = strategy.get("resolution") or "未生成"
        budget_text = _format_bool(strategy.get("budget_neutral_pass")) or "—"
        roi_keep_text = _format_bool(strategy.get("roi_quality_preserved")) or "—"
        roi_improve_text = _format_bool(strategy.get("roi_quality_improved")) or "—"
        lines.append(
            f"| {strategy['title']} | {resolution} | {bitrate_text} | "
            f"{crf_text} | {vmaf_text} | {p5_text} | {ssim_text} | "
            f"{speed_text} | {saving_text} | {general_saving_text} | "
            f"{budget_text} | {roi_keep_text} | {roi_improve_text} |"
        )
    failures = [
        strategy
        for strategy in payload["strategies"]
        if strategy.get("status") != "completed"
    ]
    if failures:
        lines.extend(["", "## 未生成项", ""])
        for strategy in failures:
            attempted = strategy.get("attempted_average_video_packet_bitrate_bps")
            attempted_text = (
                ""
                if attempted is None
                else f"；尝试码率 {attempted / 1_000_000.0:.6f} Mbit/s"
            )
            lines.append(
                f"- {strategy['title']}：{strategy.get('failure_reason', '未知原因')}{attempted_text}"
            )
    lines.extend(
        [
            "",
            "ROI 候选只能在通用无 ROI 方案的平均视频包码率预算内重新分配码率；"
            "超过预算或重点区域质量下降时标记失败，不伪造成收益。",
            "",
            "码率节省百分比只作数据记录；本报告保留负节省，不评选胜出方案，"
            "不输出部署结论，也不把软件编码结果表述为摄像头实机结果。",
            "",
        ]
    )
    (output_dir / "final_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return payload
