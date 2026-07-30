import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .errors import ToolError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Toolchain:
    ffmpeg: Path
    ffprobe: Path


def _find_executable(env_name: str, local_path: Path, command: str) -> Optional[Path]:
    configured = os.environ.get(env_name)
    if configured and Path(configured).is_file():
        return Path(configured).resolve()
    if local_path.is_file():
        return local_path.resolve()
    found = shutil.which(command)
    return Path(found).resolve() if found else None


def discover_toolchain() -> Toolchain:
    ffmpeg = _find_executable(
        "HEVC_LAB_FFMPEG",
        PROJECT_ROOT / ".tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        "ffmpeg",
    )
    ffprobe = _find_executable(
        "HEVC_LAB_FFPROBE",
        PROJECT_ROOT / ".tools" / "ffmpeg" / "bin" / "ffprobe.exe",
        "ffprobe",
    )
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if not ffprobe:
        missing.append("ffprobe")
    if missing:
        raise ToolError("缺少工具：" + "、".join(missing))
    return Toolchain(ffmpeg=ffmpeg, ffprobe=ffprobe)


def run_process(
    command: Iterable[object],
    cwd: Optional[Path] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    args = [str(item) for item in command]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "无错误输出"
        raise ToolError(f"命令执行失败（退出码 {completed.returncode}）：\n{detail[-4000:]}")
    return completed


def _has_component(output: str, name: str) -> bool:
    return re.search(rf"^\s*[A-Z.]+\s+{re.escape(name)}(?:\s|$)", output, re.MULTILINE) is not None


def check_capabilities(toolchain: Toolchain) -> dict:
    version = run_process([toolchain.ffmpeg, "-hide_banner", "-version"]).stdout.splitlines()[0]
    probe_version = run_process(
        [toolchain.ffprobe, "-hide_banner", "-version"]
    ).stdout.splitlines()[0]
    encoders = run_process([toolchain.ffmpeg, "-hide_banner", "-encoders"]).stdout
    decoders = run_process([toolchain.ffmpeg, "-hide_banner", "-decoders"]).stdout
    muxers = run_process([toolchain.ffmpeg, "-hide_banner", "-muxers"]).stdout
    demuxers = run_process([toolchain.ffmpeg, "-hide_banner", "-demuxers"]).stdout
    protocols = run_process([toolchain.ffmpeg, "-hide_banner", "-protocols"]).stdout

    if not _has_component(encoders, "libx265"):
        raise ToolError("当前 FFmpeg 不包含 libx265 编码器")
    if not _has_component(encoders, "libx264"):
        raise ToolError("当前 FFmpeg 不包含 libx264 编码器，无法生成浏览器预览")
    if not _has_component(decoders, "h264"):
        raise ToolError("当前 FFmpeg 不包含 H.264 解码器")
    if not _has_component(muxers, "hls"):
        raise ToolError("当前 FFmpeg 不包含 HLS muxer")
    if not _has_component(demuxers, "rtsp") and "rtsp" not in protocols:
        raise ToolError("当前 FFmpeg 不支持 RTSP 输入")

    return {
        "ffmpeg": version,
        "ffprobe": probe_version,
        "libx265": True,
        "libx264_preview": True,
        "h264_decoder": True,
        "rtsp_input": True,
        "hls_muxer": True,
    }
