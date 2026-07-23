from dataclasses import replace
from pathlib import Path
from typing import Optional, Tuple

from .adapters import prepare_reference, probe_video
from .core.configs import (
    default_aq_profile,
    denoise_policy_for_mode,
    resolve_encoder_conditions,
    v1_comparison_plan,
)
from .core.denoise import decide_denoise_selection
from .core.matching import match_equal_quality_candidates
from .core.models import DenoiseSettings, Toolchain
from .core.roi import ROIRegionQuality, important_regions, load_roi_settings
from .core.search import QualityThresholds
from .quality_search import run_scheme_quality_search
from .reports import (
    render_roi_overlay,
    write_denoise_study_reports,
)
from .roi_study import evaluate_important_regions


def run_denoise_study(
    toolchain: Toolchain,
    input_path: Path,
    roi_config_path: Path,
    output_dir: Path,
    mode: str = "balanced",
    scheme_name: str = "optimized",
    preset: Optional[str] = None,
    target_vmaf: Optional[float] = None,
    target_vmaf_p5: Optional[float] = None,
    target_ssim: Optional[float] = None,
    min_speed: Optional[float] = None,
    max_vmaf_delta: float = 1.0,
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
) -> dict:
    plan = v1_comparison_plan(mode)
    policy = plan.mode
    schemes = {config.name: config for config in plan.schemes}
    if scheme_name not in schemes:
        raise ValueError("降噪研究方案只能是 baseline 或 optimized")
    scheme = schemes[scheme_name]
    conditions = resolve_encoder_conditions(plan.conditions, preset)
    thresholds = QualityThresholds(
        vmaf_mean=policy.target_vmaf if target_vmaf is None else target_vmaf,
        vmaf_p5=policy.target_vmaf_p5 if target_vmaf_p5 is None else target_vmaf_p5,
        ssim=policy.target_ssim if target_ssim is None else target_ssim,
    )
    min_speed_x = policy.min_speed_x if min_speed is None else min_speed
    input_source = probe_video(toolchain.ffprobe, input_path)
    roi_settings = load_roi_settings(roi_config_path, mode)
    roi_settings.validate_input(input_source.width, input_source.height)
    denoise_settings = DenoiseSettings(
        roi=roi_settings,
        policy=denoise_policy_for_mode(mode),
    )
    output_dir = output_dir.expanduser().resolve()
    reference = prepare_reference(
        toolchain=toolchain,
        input_path=input_path,
        output_dir=output_dir,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        source=input_source,
    )
    denoise_settings.validate_input(reference.video.width, reference.video.height)

    aq_profile = default_aq_profile()
    control_scheme = replace(
        scheme,
        name=f"{scheme.name}_denoise_control",
        title=f"{scheme.title} / 无降噪 AQ2 对照",
        description="保持默认 AQ2、strength 1.0、qg-size 32，不做编码前降噪。",
    )
    denoise_scheme = replace(
        scheme,
        name=f"{scheme.name}_denoise",
        title=f"{scheme.title} / ROI 保护降噪",
        description=f"应用 {mode} 模式 hqdn3d 区域强度。",
    )
    print("正在独立搜索无降噪 AQ2 对照的画质边界……", flush=True)
    control_search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=control_scheme,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
        adaptive_quantization=aq_profile,
    )
    print("正在独立搜索 ROI 保护降噪的画质边界……", flush=True)
    denoise_search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=denoise_scheme,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
        adaptive_quantization=aq_profile,
        denoise_settings=denoise_settings,
    )
    match = match_equal_quality_candidates(
        control_search,
        denoise_search,
        max_vmaf_delta=max_vmaf_delta,
    )
    region_quality: Tuple[ROIRegionQuality, ...] = tuple()
    if match.pair:
        region_quality = evaluate_important_regions(
            toolchain,
            match.pair.baseline,
            match.pair.optimized,
            reference.video.path,
            reference.cache_key,
            important_regions(roi_settings),
            output_dir,
        )
    decision = decide_denoise_selection(match, region_quality)
    overlay_path = output_dir / "denoise_overlay.png"
    render_roi_overlay(toolchain, reference.video.path, roi_settings, overlay_path)
    return write_denoise_study_reports(
        output_dir=output_dir,
        source=input_source,
        reference=reference,
        mode=policy.to_dict(),
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        aq_profile=aq_profile,
        denoise_settings=denoise_settings,
        control_search=control_search,
        denoise_search=denoise_search,
        match=match,
        region_quality=region_quality,
        decision=decision,
        overlay_path=overlay_path,
    )
