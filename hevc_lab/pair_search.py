from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from .adapters import prepare_reference, probe_video, verify_video_continuity
from .core.configs import resolve_encoder_conditions, v1_comparison_plan
from .core.feasibility import ContinuityValidation, evaluate_feasibility
from .core.matching import match_equal_quality_candidates
from .core.search import QualityThresholds
from .core.models import Toolchain
from .core.speed import validate_speed_gate
from .quality_search import run_scheme_quality_search
from .reports import write_pair_search_reports


def run_pair_quality_search(
    toolchain: Toolchain,
    input_path: Path,
    output_dir: Path,
    mode: str = "balanced",
    preset: Optional[str] = None,
    target_vmaf: Optional[float] = None,
    target_vmaf_p5: Optional[float] = None,
    target_ssim: Optional[float] = None,
    min_speed: Optional[float] = None,
    min_algorithm_saving: Optional[float] = None,
    min_source_saving: Optional[float] = None,
    max_vmaf_delta: float = 1.0,
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    def progress(stage: str) -> None:
        if progress_callback:
            progress_callback(stage)

    plan = v1_comparison_plan(mode)
    policy = plan.mode
    conditions = resolve_encoder_conditions(plan.conditions, preset)
    thresholds = QualityThresholds(
        vmaf_mean=policy.target_vmaf if target_vmaf is None else target_vmaf,
        vmaf_p5=policy.target_vmaf_p5 if target_vmaf_p5 is None else target_vmaf_p5,
        ssim=policy.target_ssim if target_ssim is None else target_ssim,
    )
    min_speed_x = policy.min_speed_x if min_speed is None else min_speed
    effective_policy = replace(
        policy,
        min_speed_x=min_speed_x,
        min_algorithm_saving_pct=(
            policy.min_algorithm_saving_pct
            if min_algorithm_saving is None
            else min_algorithm_saving
        ),
        min_source_saving_pct=(
            policy.min_source_saving_pct
            if min_source_saving is None
            else min_source_saving
        ),
    )
    if effective_policy.min_algorithm_saving_pct < 0:
        raise ValueError("算法节省门槛不能小于0")
    if effective_policy.min_source_saving_pct < 0:
        raise ValueError("源流节省门槛不能小于0")
    validate_speed_gate(effective_policy.min_speed_x)
    effective_plan = replace(plan, mode=effective_policy)
    progress("probe_input")
    input_source = probe_video(toolchain.ffprobe, input_path)
    output_dir = output_dir.expanduser().resolve()
    progress("prepare_reference")
    reference = prepare_reference(
        toolchain=toolchain,
        input_path=input_path,
        output_dir=output_dir,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        source=input_source,
    )

    print("正在独立搜索工程基线……", flush=True)
    progress("search_baseline")
    baseline_search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=plan.baseline,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
    )
    print("正在独立搜索优化组合……", flush=True)
    progress("search_optimized")
    optimized_search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=plan.optimized,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
    )
    progress("match_equal_quality")
    match = match_equal_quality_candidates(
        baseline_search,
        optimized_search,
        max_vmaf_delta=max_vmaf_delta,
    )
    if match.pair:
        print("正在验证优化输出画面连续性……", flush=True)
        progress("validate_continuity")
        continuity = verify_video_continuity(
            toolchain=toolchain,
            output_path=Path(match.pair.optimized.output_path),
            reference=reference,
        )
    else:
        continuity = ContinuityValidation.not_checked(
            "没有等画质优化候选，无法执行输出连续性检查。"
        )
    progress("evaluate_conclusions")
    conclusions = evaluate_feasibility(
        match=match,
        mode=effective_policy,
        source_video_bitrate_bps=input_source.video_bitrate_bps,
        continuity=continuity,
    )
    progress("write_pair_reports")
    return write_pair_search_reports(
        output_dir=output_dir,
        source=input_source,
        reference=reference,
        plan=effective_plan,
        conditions=conditions,
        thresholds=thresholds,
        baseline_search=baseline_search,
        optimized_search=optimized_search,
        match=match,
        continuity=continuity,
        conclusions=conclusions,
    )
