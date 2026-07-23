import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

from ..core.models import PacketBitrateStats, VideoInfo
from ..errors import VideoError
from ..tools import run_process


def parse_fraction(value: str) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else 0.0
    return float(value)


def video_info_from_probe(path: Path, payload: Dict[str, Any]) -> VideoInfo:
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise VideoError("输入文件中没有视频流")
    fmt = payload.get("format", {})
    duration = float(video.get("duration") or fmt.get("duration") or 0.0)
    if duration <= 0:
        raise VideoError("无法读取有效视频时长")
    size = int(fmt.get("size") or path.stat().st_size)
    bitrate_value = video.get("bit_rate") or fmt.get("bit_rate")
    bitrate = float(bitrate_value) if bitrate_value else size * 8.0 / duration
    fps = parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0")
    if fps <= 0:
        raise VideoError("无法读取有效帧率")
    return VideoInfo(
        path=path.resolve(),
        codec=str(video.get("codec_name") or "unknown"),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        duration_seconds=duration,
        video_bitrate_bps=bitrate,
        file_size_bytes=size,
        pixel_format=str(video.get("pix_fmt") or "unknown"),
    )


def probe_video(ffprobe: Path, path: Path) -> VideoInfo:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise VideoError(f"输入视频不存在：{path}")
    completed = run_process(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            path,
        ]
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VideoError("ffprobe 返回了无效 JSON") from exc
    info = video_info_from_probe(path, payload)
    packet_stats = probe_video_packet_stats(
        ffprobe,
        path,
        duration_seconds=info.duration_seconds,
    )
    info = replace(info, video_bitrate_bps=packet_stats.average_bitrate_bps)
    return info


def packet_bitrate_stats_from_probe(
    payload: Dict[str, Any],
    duration_seconds: float,
    window_seconds: float = 1.0,
) -> PacketBitrateStats:
    if duration_seconds <= 0:
        raise VideoError("计算视频包码率时长必须大于 0")
    if window_seconds <= 0:
        raise VideoError("码率统计时间窗必须大于 0")
    packets = payload.get("packets", [])
    packet_bytes = 0
    timed_packets = []
    for packet in packets:
        try:
            size = int(packet.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        packet_bytes += size
        timestamp = packet.get("pts_time")
        if timestamp in {None, "N/A"}:
            timestamp = packet.get("dts_time")
        if timestamp not in {None, "N/A"}:
            try:
                timed_packets.append((float(timestamp), size))
            except (TypeError, ValueError):
                pass
    if packet_bytes <= 0:
        raise VideoError("视频流中没有可统计的压缩包字节")

    bucket_count = max(1, math.ceil(duration_seconds / window_seconds))
    bucket_bytes = [0] * bucket_count
    if timed_packets:
        origin = min(timestamp for timestamp, _ in timed_packets)
        for timestamp, size in timed_packets:
            index = int(max(0.0, timestamp - origin) // window_seconds)
            bucket_bytes[min(index, bucket_count - 1)] += size
    else:
        bucket_bytes[0] = packet_bytes
    window_bitrates = tuple(
        value * 8.0 / window_seconds for value in bucket_bytes
    )
    ordered = sorted(window_bitrates)
    p95_position = (len(ordered) - 1) * 0.95
    p95_lower = math.floor(p95_position)
    p95_upper = math.ceil(p95_position)
    p95_weight = p95_position - p95_lower
    p95_bitrate = (
        ordered[p95_lower] * (1.0 - p95_weight)
        + ordered[p95_upper] * p95_weight
    )
    return PacketBitrateStats(
        packet_count=sum(
            1
            for packet in packets
            if str(packet.get("size") or "").isdigit()
            and int(packet.get("size")) > 0
        ),
        packet_bytes=packet_bytes,
        duration_seconds=duration_seconds,
        average_bitrate_bps=packet_bytes * 8.0 / duration_seconds,
        window_seconds=window_seconds,
        window_bitrates_bps=window_bitrates,
        peak_window_bitrate_bps=max(window_bitrates),
        p95_window_bitrate_bps=p95_bitrate,
    )


def probe_video_packet_stats(
    ffprobe: Path,
    path: Path,
    duration_seconds: float = None,
    window_seconds: float = 1.0,
) -> PacketBitrateStats:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise VideoError(f"输入视频不存在：{path}")
    if duration_seconds is None:
        completed = run_process(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                path,
            ]
        )
        try:
            duration_seconds = float(
                json.loads(completed.stdout).get("format", {}).get("duration") or 0
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VideoError("无法读取视频包码率统计时长") from exc
    completed = run_process(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time,dts_time,size,flags",
            "-of",
            "json",
            path,
        ]
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VideoError("ffprobe 返回了无效视频包 JSON") from exc
    return packet_bitrate_stats_from_probe(
        payload,
        duration_seconds=duration_seconds,
        window_seconds=window_seconds,
    )


def probe_frame_timestamps(ffprobe: Path, path: Path) -> List[float]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise VideoError(f"输入视频不存在：{path}")
    completed = run_process(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            path,
        ]
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VideoError("ffprobe 返回了无效帧时间戳 JSON") from exc
    timestamps = []
    for frame in payload.get("frames", []):
        value = frame.get("best_effort_timestamp_time")
        if value not in {None, "N/A"}:
            timestamps.append(float(value))
    if not timestamps:
        raise VideoError("无法读取参考视频的逐帧时间戳")
    return timestamps
