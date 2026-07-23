import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from ..core.models import ReferenceArtifact, Toolchain, VideoInfo
from ..errors import VideoError
from ..tools import run_process
from .video_input import probe_frame_timestamps, probe_video


REFERENCE_SCHEMA_VERSION = 1
NORMALIZATION_VERSION = "ffv1-yuv420p-cfr-v1"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def normalize_clip_window(
    source: VideoInfo,
    start_seconds: float,
    duration_seconds: float,
) -> Tuple[float, int]:
    if start_seconds < 0:
        raise VideoError("片段开始时间不能小于 0")
    if duration_seconds <= 0:
        raise VideoError("片段时长必须大于 0")
    if start_seconds >= source.duration_seconds:
        raise VideoError(
            f"片段开始时间 {start_seconds:.3f}s 超出输入时长 "
            f"{source.duration_seconds:.3f}s"
        )
    available_seconds = source.duration_seconds - start_seconds
    effective_duration = min(duration_seconds, available_seconds)
    expected_frame_count = round(effective_duration * source.fps)
    if expected_frame_count < 1:
        raise VideoError("选定片段不足一帧，请增加时长或提前开始时间")
    return effective_duration, expected_frame_count


def build_reference_cache_key(
    input_sha256: str,
    source: VideoInfo,
    start_seconds: float,
    requested_duration_seconds: float,
    effective_duration_seconds: float,
) -> str:
    cache_basis = {
        "normalization_version": NORMALIZATION_VERSION,
        "input_sha256": input_sha256,
        "start_seconds": round(start_seconds, 9),
        "requested_duration_seconds": round(requested_duration_seconds, 9),
        "effective_duration_seconds": round(effective_duration_seconds, 9),
        "width": source.width,
        "height": source.height,
        "fps": round(source.fps, 9),
        "pixel_format": "yuv420p",
    }
    serialized = json.dumps(cache_basis, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def summarize_timestamps(timestamps: Iterable[float], fps: float) -> Dict[str, object]:
    values = [float(item) for item in timestamps]
    if not values:
        raise VideoError("参考视频没有可校验的帧时间戳")
    tolerance = max(0.001, 0.5 / fps)
    if abs(values[0]) > tolerance:
        raise VideoError(f"参考视频首帧时间戳未归零：{values[0]:.6f}s")
    deltas = [current - previous for previous, current in zip(values, values[1:])]
    if any(delta <= 0 for delta in deltas):
        raise VideoError("参考视频帧时间戳不是严格递增")
    digest = hashlib.sha256()
    for value in values:
        digest.update(f"{value:.9f}\n".encode("ascii"))
    return {
        "first_seconds": values[0],
        "last_seconds": values[-1],
        "min_delta_seconds": min(deltas) if deltas else 0.0,
        "max_delta_seconds": max(deltas) if deltas else 0.0,
        "timestamp_sha256": digest.hexdigest(),
        "strictly_increasing": True,
    }


def _validated_artifact(
    toolchain: Toolchain,
    source: VideoInfo,
    reference_path: Path,
    manifest_path: Path,
    input_sha256: str,
    cache_key: str,
    start_seconds: float,
    requested_duration_seconds: float,
    effective_duration_seconds: float,
    expected_frame_count: int,
    cache_hit: bool,
) -> ReferenceArtifact:
    reference_video = probe_video(toolchain.ffprobe, reference_path)
    if reference_video.codec != "ffv1":
        raise VideoError(f"参考视频编码必须为 FFV1，实际为 {reference_video.codec}")
    if reference_video.pixel_format != "yuv420p":
        raise VideoError(
            f"参考视频像素格式必须为 yuv420p，实际为 {reference_video.pixel_format}"
        )
    if (reference_video.width, reference_video.height) != (source.width, source.height):
        raise VideoError("参考视频分辨率与输入视频不一致")
    fps_tolerance = max(0.001, source.fps * 0.0001)
    if abs(reference_video.fps - source.fps) > fps_tolerance:
        raise VideoError(
            f"参考视频帧率 {reference_video.fps:.6f} 与输入帧率 "
            f"{source.fps:.6f} 不一致"
        )
    timestamps = probe_frame_timestamps(toolchain.ffprobe, reference_path)
    frame_count = len(timestamps)
    if abs(frame_count - expected_frame_count) > 1:
        raise VideoError(
            f"参考视频帧数校验失败：期望约 {expected_frame_count} 帧，"
            f"实际 {frame_count} 帧"
        )
    timestamp_summary = summarize_timestamps(timestamps, reference_video.fps)
    return ReferenceArtifact(
        input_path=source.path,
        input_sha256=input_sha256,
        cache_key=cache_key,
        requested_start_seconds=start_seconds,
        requested_duration_seconds=requested_duration_seconds,
        effective_duration_seconds=effective_duration_seconds,
        expected_frame_count=expected_frame_count,
        frame_count=frame_count,
        video=reference_video,
        timestamp_summary=timestamp_summary,
        manifest_path=manifest_path,
        cache_hit=cache_hit,
    )


def _load_cached_artifact(
    toolchain: Toolchain,
    source: VideoInfo,
    reference_path: Path,
    manifest_path: Path,
    input_sha256: str,
    cache_key: str,
    start_seconds: float,
    requested_duration_seconds: float,
    effective_duration_seconds: float,
    expected_frame_count: int,
) -> Optional[ReferenceArtifact]:
    if not reference_path.is_file() or not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("cache_key") != cache_key:
            return None
        return _validated_artifact(
            toolchain=toolchain,
            source=source,
            reference_path=reference_path,
            manifest_path=manifest_path,
            input_sha256=input_sha256,
            cache_key=cache_key,
            start_seconds=start_seconds,
            requested_duration_seconds=requested_duration_seconds,
            effective_duration_seconds=effective_duration_seconds,
            expected_frame_count=expected_frame_count,
            cache_hit=True,
        )
    except (OSError, ValueError, VideoError):
        return None


def prepare_reference(
    toolchain: Toolchain,
    input_path: Path,
    output_dir: Path,
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
    source: Optional[VideoInfo] = None,
) -> ReferenceArtifact:
    input_path = input_path.expanduser().resolve()
    source = source or probe_video(toolchain.ffprobe, input_path)
    if source.path != input_path:
        raise VideoError("参考准备的输入路径与已探测视频不一致")
    effective_duration, expected_frame_count = normalize_clip_window(
        source,
        start_seconds,
        duration_seconds,
    )
    input_sha256 = sha256_file(input_path)
    cache_key = build_reference_cache_key(
        input_sha256,
        source,
        start_seconds,
        duration_seconds,
        effective_duration,
    )

    reference_dir = output_dir.expanduser().resolve() / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    reference_path = reference_dir / "reference_lossless.mkv"
    manifest_path = reference_dir / "reference.json"
    cached = _load_cached_artifact(
        toolchain,
        source,
        reference_path,
        manifest_path,
        input_sha256,
        cache_key,
        start_seconds,
        duration_seconds,
        effective_duration,
        expected_frame_count,
    )
    if cached:
        return cached

    temporary_path = reference_dir / "reference_lossless.part.mkv"
    temporary_path.unlink(missing_ok=True)
    fps_text = f"{source.fps:.12g}"
    filters = (
        "setpts=PTS-STARTPTS,"
        f"fps=fps={fps_text}:start_time=0:round=near:eof_action=pass,"
        "format=yuv420p"
    )
    completed = run_process(
        [
            toolchain.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            input_path,
            "-ss",
            f"{start_seconds:.9f}",
            "-t",
            f"{effective_duration:.9f}",
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            filters,
            "-frames:v",
            str(expected_frame_count),
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-g",
            "1",
            temporary_path,
        ]
    )
    (reference_dir / "prepare.log").write_text(
        completed.stdout + "\n" + completed.stderr,
        encoding="utf-8",
    )
    if not temporary_path.is_file():
        raise VideoError("未生成 FFV1 无损参考视频")
    temporary_path.replace(reference_path)
    artifact = _validated_artifact(
        toolchain=toolchain,
        source=source,
        reference_path=reference_path,
        manifest_path=manifest_path,
        input_sha256=input_sha256,
        cache_key=cache_key,
        start_seconds=start_seconds,
        requested_duration_seconds=duration_seconds,
        effective_duration_seconds=effective_duration,
        expected_frame_count=expected_frame_count,
        cache_hit=False,
    )
    manifest_payload = {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        **artifact.to_dict(),
    }
    manifest_payload["cache_hit"] = False
    manifest_temp = reference_dir / "reference.part.json"
    manifest_temp.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_temp.replace(manifest_path)
    return replace(artifact, manifest_path=manifest_path)
