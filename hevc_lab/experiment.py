import shutil
from pathlib import Path
from typing import List, Optional

from .adapters import generate_sample, prepare_reference, probe_video
from .core.configs import resolve_encoder_conditions, v1_comparison_plan
from .core.models import CandidateResult, Toolchain
from .core.selection import calculate_saving, is_deployable, select_candidate
from .core.speed import classify_speed, speed_gate_passes
from .encoders import encode_candidate
from .metrics import compute_quality
from .reports import write_reports


def run_experiment(
    toolchain: Toolchain,
    input_path: Path,
    output_dir: Path,
    crf: Optional[float] = None,
    preset: Optional[str] = None,
    target_vmaf: Optional[float] = None,
    target_vmaf_p5: Optional[float] = None,
    target_ssim: Optional[float] = None,
    min_speed: Optional[float] = None,
    min_algorithm_saving: Optional[float] = None,
    min_source_saving: Optional[float] = None,
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
    mode: str = "balanced",
) -> dict:
    comparison_plan = v1_comparison_plan(mode)
    policy = comparison_plan.mode
    overrides = {
        "crf": crf is not None,
        "preset": preset is not None,
        "target_vmaf": target_vmaf is not None,
        "target_vmaf_p5": target_vmaf_p5 is not None,
        "target_ssim": target_ssim is not None,
        "min_speed_x": min_speed is not None,
        "min_algorithm_saving_pct": min_algorithm_saving is not None,
        "min_source_saving_pct": min_source_saving is not None,
    }
    crf = policy.default_crf if crf is None else crf
    target_vmaf = policy.target_vmaf if target_vmaf is None else target_vmaf
    target_vmaf_p5 = policy.target_vmaf_p5 if target_vmaf_p5 is None else target_vmaf_p5
    target_ssim = policy.target_ssim if target_ssim is None else target_ssim
    min_speed = policy.min_speed_x if min_speed is None else min_speed
    min_algorithm_saving = (
        policy.min_algorithm_saving_pct
        if min_algorithm_saving is None
        else min_algorithm_saving
    )
    min_source_saving = (
        policy.min_source_saving_pct
        if min_source_saving is None
        else min_source_saving
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
    source = reference.video
    candidate_dir = output_dir / "work" / "candidates"
    log_root = output_dir / "work" / "logs"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)

    results: List[CandidateResult] = []
    conditions = resolve_encoder_conditions(comparison_plan.conditions, preset)
    configs = list(comparison_plan.schemes)
    for index, config in enumerate(configs, start=1):
        print(f"[{index}/{len(configs)}] {config.title}：正在编码……", flush=True)
        candidate_path = candidate_dir / f"{index:02d}_{mode}_{config.name}.mp4"
        candidate_log_dir = log_root / f"{index:02d}_{mode}_{config.name}"
        candidate_log_dir.mkdir(parents=True, exist_ok=True)
        timing = encode_candidate(
            toolchain,
            source,
            config,
            candidate_path,
            candidate_log_dir / "encode.log",
            crf,
            conditions=conditions,
        )
        encoded = probe_video(toolchain.ffprobe, candidate_path)
        print(f"[{index}/{len(configs)}] {config.title}：正在计算 VMAF/SSIM……", flush=True)
        vmaf_mean, vmaf_p5, ssim = compute_quality(
            toolchain,
            candidate_path,
            source.path,
            candidate_log_dir,
        )
        quality_pass = (
            vmaf_mean >= target_vmaf
            and vmaf_p5 >= target_vmaf_p5
            and ssim >= target_ssim
        )
        speed_pass = speed_gate_passes(timing["speed"], min_speed)
        result = CandidateResult(
            name=config.name,
            title=config.title,
            description=config.description,
            output_path=str(candidate_path),
            x265_params=config.x265_params(source.fps),
            crf=crf,
            preset=conditions.preset,
            bitrate_bps=encoded.video_bitrate_bps,
            file_size_bytes=encoded.file_size_bytes,
            vmaf_mean=vmaf_mean,
            vmaf_p5=vmaf_p5,
            ssim=ssim,
            encode_seconds=timing["elapsed"],
            encode_speed_x=timing["speed"],
            quality_pass=quality_pass,
            speed_pass=speed_pass,
            eligible=quality_pass and speed_pass,
            speed_tier=classify_speed(timing["speed"], min_speed),
            bitrate_saving_vs_source_pct=calculate_saving(
                input_source.video_bitrate_bps,
                encoded.video_bitrate_bps,
            ),
        )
        results.append(result)
        outcome = "合格" if result.eligible else "不合格"
        print(
            f"[{index}/{len(configs)}] VMAF={vmaf_mean:.3f}，P5={vmaf_p5:.3f}，"
            f"SSIM={ssim:.6f}，码率={encoded.video_bitrate_bps / 1_000_000:.3f} Mbit/s，{outcome}",
            flush=True,
        )

    baseline = next(item for item in results if item.name == "baseline")
    for result in results:
        result.bitrate_saving_vs_baseline_pct = calculate_saving(
            baseline.bitrate_bps,
            result.bitrate_bps,
        )
    best_candidate = select_candidate(
        item for item in results if item.name != "baseline"
    )
    selected = (
        best_candidate
        if is_deployable(
            best_candidate,
            min_source_saving,
            min_algorithm_saving,
        )
        else None
    )
    for stale_output in output_dir.glob("selected_*.mp4"):
        stale_output.unlink()
    selected_output = None
    if selected:
        selected_output = output_dir / f"selected_{mode}_{selected.name}.mp4"
        shutil.copy2(Path(selected.output_path), selected_output)

    settings = {
        "mode": policy.to_dict(),
        "overrides": overrides,
        "crf": crf,
        "preset": conditions.preset,
        "encoder_conditions": conditions.to_dict(),
        "target_vmaf": target_vmaf,
        "target_vmaf_p5": target_vmaf_p5,
        "target_ssim": target_ssim,
        "min_speed_x": min_speed,
        "min_algorithm_saving_pct": min_algorithm_saving,
        "min_source_saving_pct": min_source_saving,
        "start_seconds": start_seconds,
        "requested_duration_seconds": duration_seconds,
        "reference_cache_hit": reference.cache_hit,
        "comparison_rule": "同一模式内固定 CRF 与 preset，仅改变帧间预测参数",
    }
    report = write_reports(
        output_dir=output_dir,
        source=input_source,
        reference=reference,
        settings=settings,
        configs=configs,
        candidates=results,
        best_candidate=best_candidate,
        selected=selected,
        selected_output=selected_output,
    )
    return report
