from pathlib import Path
from typing import Any, Dict

from ..adapters import probe_video
from ..core.models import Toolchain
from ..errors import VideoError
from ..tools import run_process


def generate_browser_preview(
    toolchain: Toolchain,
    source: Path,
    destination: Path,
) -> Dict[str, Any]:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    source_info = probe_video(toolchain.ffprobe, source)
    run_process(
        [
            toolchain.ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "12",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            destination,
        ]
    )
    preview_info = probe_video(toolchain.ffprobe, destination)
    if (preview_info.width, preview_info.height) != (
        source_info.width,
        source_info.height,
    ):
        raise VideoError("浏览器预览分辨率与正式 H.265 输出不一致")
    tolerance = max(0.25, source_info.duration_seconds * 0.02)
    if abs(preview_info.duration_seconds - source_info.duration_seconds) > tolerance:
        raise VideoError("浏览器预览时长与正式 H.265 输出不一致")
    return {
        "path": str(destination),
        "codec": preview_info.codec,
        "width": preview_info.width,
        "height": preview_info.height,
        "duration_seconds": preview_info.duration_seconds,
        "note": "H.264 浏览器预览仅用于观看，不参与 H.265 码率和画质指标",
    }
