from dataclasses import replace
from pathlib import Path
from typing import Optional

from .adapters import prepare_reference, probe_video
from .core.configs import resolve_encoder_conditions, v1_comparison_plan
from .core.matching import match_equal_quality_candidates
from .core.models import Toolchain
from .core.preset import decide_preset_study
from .core.search import QualityThresholds
from .core.speed import validate_speed_gate
from .quality_search import run_scheme_quality_search
from .reports import write_preset_study_reports


def run_preset_study(
    toolchain: Toolchain,
    input_path: Path,
    output_dir: Path,
    mode: str = "aggressive",
    scheme_name: str = "optimized",
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
        raise ValueError("preset 研究方案只能是 baseline 或 optimized")
    scheme = schemes[scheme_name]
    thresholds = QualityThresholds(
        vmaf_mean=policy.target_vmaf if target_vmaf is None else target_vmaf,
        vmaf_p5=policy.target_vmaf_p5 if target_vmaf_p5 is None else target_vmaf_p5,
        ssim=policy.target_ssim if target_ssim is None else target_ssim,
    )
    min_speed_x = policy.min_speed_x if min_speed is None else min_speed
    validate_speed_gate(min_speed_x)

    medium_conditions = resolve_encoder_conditions(plan.conditions, "medium")
    slow_conditions = (
        plan.conditions
        if plan.conditions.preset == "slow"
        else resolve_encoder_conditions(plan.conditions, "slow")
    )
    medium_scheme = replace(
        scheme,
        name=f"{scheme.name}_preset_medium",
        title=f"{scheme.title} / x265 medium 对照",
        description="固定其余编码条件，仅使用 x265 medium preset。",
    )
    slow_scheme = replace(
        scheme,
        name=f"{scheme.name}_preset_slow",
        title=f"{scheme.title} / x265 slow 候选",
        description="固定其余编码条件，仅使用 x265 slow preset。",
    )

    input_source = probe_video(toolchain.ffprobe, input_path)
    output_dir = output_dir.expanduser().resolve()
    reference = prepare_reference(
        toolchain=toolchain,
        input_path=input_path,
        output_dir=output_dir,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        source=input_source,
    )

    print("正在独立搜索 x265 medium 对照的画质边界……", flush=True)
    medium_search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=medium_scheme,
        conditions=medium_conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
    )
    print("正在独立搜索 x265 slow 候选的画质边界……", flush=True)
    slow_search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=slow_scheme,
        conditions=slow_conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
    )
    match = match_equal_quality_candidates(
        medium_search,
        slow_search,
        max_vmaf_delta=max_vmaf_delta,
    )
    decision = decide_preset_study(match)
    return write_preset_study_reports(
        output_dir=output_dir,
        source=input_source,
        reference=reference,
        mode=policy.to_dict(),
        scheme=scheme,
        medium_conditions=medium_conditions,
        slow_conditions=slow_conditions,
        thresholds=thresholds,
        medium_search=medium_search,
        slow_search=slow_search,
        match=match,
        decision=decision,
    )
