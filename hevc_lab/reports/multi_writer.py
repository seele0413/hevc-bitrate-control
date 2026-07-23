import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def _format_bitrate_mbps(value):
    return "" if value is None else f"{value / 1_000_000.0:.6f}"


def _format_saving(value):
    return "" if value is None else f"{value:.2f}"


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
        "saving_vs_default_pct",
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
                    "saving_vs_default_pct": _format_saving(
                        strategy.get("saving_vs_default_pct")
                    ),
                }
            )

    lines = [
        "# x265默认编码与三档综合策略最终数据",
        "",
        "| 编码策略 | 输出分辨率 | 平均视频包码率 | 相对默认方案节省 |",
        "|---|---|---:|---:|",
    ]
    for strategy in payload["strategies"]:
        bitrate = strategy.get("average_video_packet_bitrate_bps")
        if bitrate is None:
            bitrate_text = "未生成"
        else:
            bitrate_text = f"{bitrate / 1_000_000.0:.6f} Mbit/s"
        saving = strategy.get("saving_vs_default_pct")
        saving_text = "—" if saving is None else f"{saving:.2f}%"
        resolution = strategy.get("resolution") or "未生成"
        lines.append(
            f"| {strategy['title']} | {resolution} | {bitrate_text} | {saving_text} |"
        )
    failures = [
        strategy
        for strategy in payload["strategies"]
        if strategy.get("status") != "completed"
    ]
    if failures:
        lines.extend(["", "## 未生成项", ""])
        for strategy in failures:
            lines.append(
                f"- {strategy['title']}：{strategy.get('failure_reason', '未知原因')}"
            )
    lines.extend(
        [
            "",
            "码率节省百分比只作数据记录；本报告不评选胜出方案，"
            "不输出部署结论，也不把软件编码结果表述为摄像头实机结果。",
            "",
        ]
    )
    (output_dir / "final_summary.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return payload
