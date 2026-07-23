import os
from pathlib import Path

from ..core.feasibility import ContinuityValidation, assess_video_continuity
from ..core.models import ReferenceArtifact, Toolchain
from ..errors import ToolError, VideoError
from ..tools import run_process
from .video_input import probe_frame_timestamps, probe_video


def verify_video_continuity(
    toolchain: Toolchain,
    output_path: Path,
    reference: ReferenceArtifact,
) -> ContinuityValidation:
    output_path = output_path.expanduser().resolve()
    if not output_path.is_file():
        return ContinuityValidation.not_checked(f"输出视频不存在：{output_path}")
    decoded = run_process(
        [
            toolchain.ffmpeg,
            "-v",
            "error",
            "-xerror",
            "-i",
            output_path,
            "-map",
            "0:v:0",
            "-an",
            "-f",
            "null",
            os.devnull,
        ],
        check=False,
    )
    decode_error = decoded.stderr.strip() or decoded.stdout.strip()
    try:
        output = probe_video(toolchain.ffprobe, output_path)
        timestamps = probe_frame_timestamps(toolchain.ffprobe, output_path)
    except (OSError, ToolError, VideoError, ValueError) as exc:
        return ContinuityValidation(
            checked=True,
            passed=False,
            output_path=str(output_path),
            reason=f"无法完成输出规格或时间戳检查：{exc}",
            checks={
                "decoded_without_error": decoded.returncode == 0,
                "metadata_and_timestamps_readable": False,
            },
            metrics={},
        )
    return assess_video_continuity(
        reference=reference,
        output=output,
        timestamps=timestamps,
        decoded_without_error=decoded.returncode == 0,
        decode_error=decode_error,
    )
