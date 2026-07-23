import time
from pathlib import Path
from typing import Dict, Optional

from ..core.models import (
    AdaptiveQuantizationSettings,
    DenoiseSettings,
    EncoderConditions,
    InterConfig,
    RateControlSettings,
    ROISettings,
    Toolchain,
    VideoInfo,
)
from ..tools import run_process


def combined_roi_denoise_filter(
    roi_settings: ROISettings,
    denoise_settings: DenoiseSettings,
    output_label: str = "filtered",
) -> str:
    """先完成 ROI 分区降噪，再给合成帧附加 ROI QP side data。"""
    if roi_settings.config_hash != denoise_settings.roi.config_hash:
        raise ValueError("ROI QP 与降噪必须使用同一份 ROI 配置")
    if roi_settings.policy.mode != denoise_settings.policy.mode:
        raise ValueError("ROI QP 与降噪必须使用同一模式策略")
    pre_roi_label = "denoised_pre_roi"
    return (
        denoise_settings.filter_complex(output_label=pre_roi_label)
        + f";[{pre_roi_label}]"
        + roi_settings.filter_chain()
        + f"[{output_label}]"
    )


def build_x265_params(
    config: InterConfig,
    fps: float,
    rate_control: Optional[RateControlSettings] = None,
    adaptive_quantization: Optional[AdaptiveQuantizationSettings] = None,
) -> str:
    groups = [config.x265_params(fps)]
    if rate_control and rate_control.enabled:
        groups.append(rate_control.x265_params())
    if adaptive_quantization:
        groups.append(adaptive_quantization.x265_params())
    return ":".join(item for item in groups if item)


def encode_candidate(
    toolchain: Toolchain,
    source: VideoInfo,
    config: InterConfig,
    destination: Path,
    log_path: Path,
    crf: float,
    preset: Optional[str] = None,
    conditions: Optional[EncoderConditions] = None,
    rate_control: Optional[RateControlSettings] = None,
    adaptive_quantization: Optional[AdaptiveQuantizationSettings] = None,
    roi_settings: Optional[ROISettings] = None,
    denoise_settings: Optional[DenoiseSettings] = None,
) -> Dict[str, float]:
    conditions = conditions or EncoderConditions(preset=preset or "medium")
    if preset is not None and preset != conditions.preset:
        raise ValueError("preset 与统一编码条件不一致")
    params = build_x265_params(
        config,
        source.fps,
        rate_control,
        adaptive_quantization,
    )
    if roi_settings:
        roi_settings.validate_input(source.width, source.height)
    if denoise_settings:
        denoise_settings.validate_input(source.width, source.height)
    command = [
        toolchain.ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        source.path,
    ]
    if denoise_settings and roi_settings:
        command.extend(
            [
                "-filter_complex",
                combined_roi_denoise_filter(roi_settings, denoise_settings),
                "-map",
                "[filtered]",
            ]
        )
    elif denoise_settings:
        command.extend(
            [
                "-filter_complex",
                denoise_settings.filter_complex(),
                "-map",
                "[denoised]",
            ]
        )
    else:
        command.extend(["-map", "0:v:0"])
    command.append("-an")
    if roi_settings and not denoise_settings:
        command.extend(["-vf", roi_settings.filter_chain()])
    command.extend([
        "-c:v",
        conditions.encoder,
        "-preset",
        conditions.preset,
        "-crf",
        str(crf),
        "-profile:v",
        conditions.profile,
        "-pix_fmt",
        conditions.pixel_format,
        "-x265-params",
        params,
        "-movflags",
        "+faststart",
        destination,
    ])
    started = time.perf_counter()
    completed = run_process(command)
    elapsed = max(time.perf_counter() - started, 0.001)
    log_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    return {
        "elapsed": elapsed,
        "speed": source.duration_seconds / elapsed,
    }


def encode_default_x265(
    toolchain: Toolchain,
    source: VideoInfo,
    destination: Path,
    log_path: Path,
) -> Dict[str, float]:
    """只选择 libx265，保留 FFmpeg/libx265 的原生默认编码参数。"""
    command = [
        toolchain.ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        source.path,
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx265",
        "-movflags",
        "+faststart",
        destination,
    ]
    started = time.perf_counter()
    completed = run_process(command)
    elapsed = max(time.perf_counter() - started, 0.001)
    log_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    return {
        "elapsed": elapsed,
        "speed": source.duration_seconds / elapsed,
    }
