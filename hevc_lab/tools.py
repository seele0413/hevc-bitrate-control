import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from .errors import ToolError
from .core.models import Toolchain


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    configured_model = os.environ.get("HEVC_LAB_VMAF_MODEL")
    model = (
        Path(configured_model).resolve()
        if configured_model
        else PROJECT_ROOT / ".tools" / "vmaf" / "model" / "vmaf_v0.6.1.json"
    )
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if not ffprobe:
        missing.append("ffprobe")
    if not model.is_file():
        missing.append(f"VMAF 模型({model})")
    if missing:
        raise ToolError("缺少工具：" + "、".join(missing))
    return Toolchain(ffmpeg=ffmpeg, ffprobe=ffprobe, vmaf_model=model.resolve())


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


def check_capabilities(toolchain: Toolchain) -> dict:
    version = run_process([toolchain.ffmpeg, "-hide_banner", "-version"]).stdout.splitlines()[0]
    encoders = run_process([toolchain.ffmpeg, "-hide_banner", "-encoders"]).stdout
    filters = run_process([toolchain.ffmpeg, "-hide_banner", "-filters"]).stdout
    if "libx265" not in encoders:
        raise ToolError("当前 FFmpeg 不包含 libx265 编码器")
    if "libx264" not in encoders:
        raise ToolError("当前 FFmpeg 不包含 libx264 编码器，无法生成浏览器 H.264 预览")
    if "libvmaf" not in filters:
        raise ToolError("当前 FFmpeg 不包含 libvmaf 滤镜")
    if "addroi" not in filters:
        raise ToolError("当前 FFmpeg 不包含 addroi 滤镜")
    if "hqdn3d" not in filters:
        raise ToolError("当前 FFmpeg 不包含 hqdn3d 滤镜")
    probe_version = run_process([toolchain.ffprobe, "-hide_banner", "-version"]).stdout.splitlines()[0]
    return {
        "ffmpeg": version,
        "ffprobe": probe_version,
        "libx265": True,
        "libx264": True,
        "libvmaf": True,
        "addroi": True,
        "hqdn3d": True,
        "vmaf_model": str(toolchain.vmaf_model),
    }
