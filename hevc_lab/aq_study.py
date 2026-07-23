from dataclasses import replace
from pathlib import Path
from typing import Optional

from .adapters import prepare_reference, probe_video
from .core.aq import AdaptiveQuantizationTrial, select_best_aq_trial
from .core.configs import (
    aq_profiles_for_mode,
    default_aq_profile,
    resolve_encoder_conditions,
    v1_comparison_plan,
)
from .core.matching import match_equal_quality_candidates
from .core.models import Toolchain
from .core.search import QualityThresholds
from .quality_search import run_scheme_quality_search
from .reports import write_aq_study_reports


def run_aq_study(
    toolchain: Toolchain,
    input_path: Path,
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
        raise ValueError("AQ 研究方案只能是 baseline 或 optimized")
    scheme = schemes[scheme_name]
    conditions = resolve_encoder_conditions(plan.conditions, preset)
    thresholds = QualityThresholds(
        vmaf_mean=policy.target_vmaf if target_vmaf is None else target_vmaf,
        vmaf_p5=policy.target_vmaf_p5 if target_vmaf_p5 is None else target_vmaf_p5,
        ssim=policy.target_ssim if target_ssim is None else target_ssim,
    )
    min_speed_x = policy.min_speed_x if min_speed is None else min_speed
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

    control_profile = default_aq_profile()
    control_scheme = replace(
        scheme,
        name=f"{scheme.name}_aq_default",
        title=f"{scheme.title} / {control_profile.title}",
        description=control_profile.description,
    )
    print("正在搜索默认 AQ2 对照的画质边界……", flush=True)
    control_search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=control_scheme,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
        adaptive_quantization=control_profile,
    )

    trials = []
    if control_search.selected is not None:
        for profile in aq_profiles_for_mode(mode):
            profile_scheme = replace(
                scheme,
                name=f"{scheme.name}_aq_{profile.name}",
                title=f"{scheme.title} / {profile.title}",
                description=profile.description,
            )
            print(f"正在独立搜索{profile.title}的画质边界……", flush=True)
            search = run_scheme_quality_search(
                toolchain=toolchain,
                input_source=input_source,
                reference=reference,
                output_dir=output_dir,
                scheme=profile_scheme,
                conditions=conditions,
                thresholds=thresholds,
                min_speed_x=min_speed_x,
                adaptive_quantization=profile,
            )
            match = match_equal_quality_candidates(
                control_search,
                search,
                max_vmaf_delta=max_vmaf_delta,
            )
            trial = AdaptiveQuantizationTrial(profile, search, match)
            trials.append(trial)
            if match.pair:
                print(
                    f"[{profile.name}] |ΔVMAF|={match.pair.vmaf_delta:.3f}，"
                    f"平均码率变化={match.pair.algorithm_saving_pct:.2f}%",
                    flush=True,
                )
            else:
                print(f"[{profile.name}] 证据不足：{match.reason}", flush=True)

    selected = select_best_aq_trial(trials)
    return write_aq_study_reports(
        output_dir=output_dir,
        source=input_source,
        reference=reference,
        mode=policy.to_dict(),
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        control_profile=control_profile,
        control_search=control_search,
        trials=trials,
        selected=selected,
    )
