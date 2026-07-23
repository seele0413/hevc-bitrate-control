import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Iterable, Optional, Tuple

from ..core.models import Toolchain
from ..errors import ToolError
from ..tools import run_process


SSIM_PATTERN = re.compile(r"All:([0-9.]+)")


def percentile(values: Iterable[float], percent: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("没有可计算百分位的数据")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def parse_vmaf_json(path: Path) -> Tuple[float, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pooled = payload.get("pooled_metrics", {}).get("vmaf", {})
    mean = pooled.get("mean")
    frame_scores = [
        frame.get("metrics", {}).get("vmaf")
        for frame in payload.get("frames", [])
        if frame.get("metrics", {}).get("vmaf") is not None
    ]
    if mean is None:
        if not frame_scores:
            raise ToolError("VMAF 日志中没有有效分数")
        mean = sum(frame_scores) / len(frame_scores)
    p5 = percentile(frame_scores, 5) if frame_scores else float(pooled.get("min", mean))
    return float(mean), float(p5)


def parse_ssim_output(text: str) -> float:
    matches = SSIM_PATTERN.findall(text)
    if not matches:
        raise ToolError("无法从 FFmpeg 输出中解析 SSIM")
    return float(matches[-1])


def compute_quality(
    toolchain: Toolchain,
    distorted: Path,
    reference: Path,
    log_dir: Path,
    crop: Optional[Tuple[int, int, int, int]] = None,
) -> Tuple[float, float, float]:
    log_dir.mkdir(parents=True, exist_ok=True)
    model_copy = log_dir / "vmaf_model.json"
    shutil.copy2(toolchain.vmaf_model, model_copy)
    vmaf_log = log_dir / "vmaf.json"
    crop_filter = ""
    if crop is not None:
        x, y, width, height = crop
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("画质裁剪区域无效")
        crop_filter = f"crop={width}:{height}:{x}:{y},"
    vmaf_filter = (
        f"[0:v]{crop_filter}setpts=PTS-STARTPTS[dist];"
        f"[1:v]{crop_filter}setpts=PTS-STARTPTS[ref];"
        "[dist][ref]libvmaf=log_fmt=json:log_path=vmaf.json:"
        "model=path=vmaf_model.json:n_threads=0"
    )
    vmaf_result = run_process(
        [
            toolchain.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            distorted,
            "-i",
            reference,
            "-lavfi",
            vmaf_filter,
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ],
        cwd=log_dir,
    )
    (log_dir / "vmaf_ffmpeg.log").write_text(
        vmaf_result.stdout + "\n" + vmaf_result.stderr,
        encoding="utf-8",
    )
    if not vmaf_log.is_file():
        raise ToolError("FFmpeg 未生成 VMAF JSON 日志")
    vmaf_mean, vmaf_p5 = parse_vmaf_json(vmaf_log)

    ssim_filter = (
        f"[0:v]{crop_filter}setpts=PTS-STARTPTS[dist];"
        f"[1:v]{crop_filter}setpts=PTS-STARTPTS[ref];"
        "[dist][ref]ssim"
    )
    ssim_result = run_process(
        [
            toolchain.ffmpeg,
            "-hide_banner",
            "-i",
            distorted,
            "-i",
            reference,
            "-lavfi",
            ssim_filter,
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ],
        cwd=log_dir,
    )
    (log_dir / "ssim_ffmpeg.log").write_text(
        ssim_result.stdout + "\n" + ssim_result.stderr,
        encoding="utf-8",
    )
    ssim = parse_ssim_output(ssim_result.stderr + "\n" + ssim_result.stdout)
    return vmaf_mean, vmaf_p5, ssim
