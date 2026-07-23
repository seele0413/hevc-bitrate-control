from pathlib import Path
from typing import List, Optional

from .adapters import prepare_reference, probe_video, probe_video_packet_stats
from .adapters.reference import sha256_file
from .core.configs import resolve_encoder_conditions, v1_comparison_plan
from .core.models import CandidateResult, Toolchain
from .core.rate_control import (
    RateControlTrial,
    derive_vbv_settings,
    first_quality_preserving_trial,
    quality_is_preserved,
    vbv_ratio_candidates,
)
from .core.search import QualityThresholds
from .core.selection import calculate_saving
from .core.speed import classify_speed, speed_gate_passes
from .encoders import build_x265_params, encode_candidate
from .metrics import compute_quality
from .quality_search import (
    _load_cached_candidate,
    _write_candidate_manifest,
    build_candidate_cache_key,
    run_scheme_quality_search,
)
from .reports import write_rate_control_reports


def run_rate_control_study(
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
    maximum_peak_ratio: float = 2.5,
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
) -> dict:
    plan = v1_comparison_plan(mode)
    policy = plan.mode
    schemes = {config.name: config for config in plan.schemes}
    if scheme_name not in schemes:
        raise ValueError("码控研究方案只能是 baseline 或 optimized")
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

    print("正在搜索无上限 CRF 画质边界……", flush=True)
    search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
    )
    uncapped = search.selected
    if uncapped is None:
        return write_rate_control_reports(
            output_dir,
            input_source,
            reference,
            policy.to_dict(),
            scheme,
            conditions,
            thresholds,
            search,
            None,
            None,
            [],
            None,
        )
    uncapped_stats = probe_video_packet_stats(
        toolchain.ffprobe,
        Path(uncapped.output_path),
        duration_seconds=reference.effective_duration_seconds,
    )

    candidate_dir = output_dir / "rate_control" / "candidates"
    log_root = output_dir / "rate_control" / "logs"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)
    vmaf_model_sha256 = sha256_file(toolchain.vmaf_model)
    trials: List[RateControlTrial] = []
    for ratio in vbv_ratio_candidates(policy, maximum_ratio=maximum_peak_ratio):
        settings = derive_vbv_settings(
            uncapped_stats.average_bitrate_bps,
            ratio,
            policy.vbv_buffer_seconds,
        )
        tag = f"ratio_{ratio:.2f}_{settings.vbv_maxrate_kbps}k".replace(".", "_")
        candidate_path = (candidate_dir / f"{tag}.mp4").resolve()
        manifest_path = candidate_dir / f"{tag}.json"
        cache_key = build_candidate_cache_key(
            input_sha256=reference.input_sha256,
            reference_cache_key=reference.cache_key,
            config=scheme,
            conditions=conditions,
            fps=reference.video.fps,
            crf=uncapped.crf,
            vmaf_model_sha256=vmaf_model_sha256,
            min_speed_x=min_speed_x,
            rate_control=settings,
        )
        candidate = _load_cached_candidate(
            manifest_path,
            candidate_path,
            cache_key,
            thresholds,
            min_speed_x,
        )
        if candidate:
            print(f"[VBV {ratio:.2f}x] 缓存命中", flush=True)
        else:
            log_dir = log_root / tag
            log_dir.mkdir(parents=True, exist_ok=True)
            print(
                f"[VBV {ratio:.2f}x] maxrate={settings.vbv_maxrate_kbps} kbit/s，正在编码……",
                flush=True,
            )
            timing = encode_candidate(
                toolchain=toolchain,
                source=reference.video,
                config=scheme,
                destination=candidate_path,
                log_path=log_dir / "encode.log",
                crf=uncapped.crf,
                conditions=conditions,
                rate_control=settings,
            )
            encoded = probe_video(toolchain.ffprobe, candidate_path)
            vmaf_mean, vmaf_p5, ssim = compute_quality(
                toolchain,
                candidate_path,
                reference.video.path,
                log_dir,
            )
            candidate = CandidateResult(
                name="capped_crf",
                title=f"{scheme.title} Capped CRF",
                description="CRF 画质优先并使用 VBV 保护局部峰值码率。",
                output_path=str(candidate_path),
                x265_params=build_x265_params(
                    scheme,
                    reference.video.fps,
                    settings,
                ),
                crf=uncapped.crf,
                preset=conditions.preset,
                bitrate_bps=encoded.video_bitrate_bps,
                file_size_bytes=encoded.file_size_bytes,
                vmaf_mean=vmaf_mean,
                vmaf_p5=vmaf_p5,
                ssim=ssim,
                encode_seconds=timing["elapsed"],
                encode_speed_x=timing["speed"],
                quality_pass=False,
                speed_pass=speed_gate_passes(timing["speed"], min_speed_x),
                eligible=False,
                speed_tier=classify_speed(timing["speed"], min_speed_x),
                bitrate_saving_vs_source_pct=calculate_saving(
                    input_source.video_bitrate_bps,
                    encoded.video_bitrate_bps,
                ),
            )
            candidate.quality_pass = thresholds.accepts(candidate)
            candidate.eligible = candidate.quality_pass and candidate.speed_pass
            _write_candidate_manifest(
                manifest_path,
                cache_key,
                reference,
                scheme,
                conditions,
                candidate,
                settings,
                min_speed_x=min_speed_x,
            )
        packet_stats = probe_video_packet_stats(
            toolchain.ffprobe,
            candidate_path,
            duration_seconds=reference.effective_duration_seconds,
        )
        trial = RateControlTrial(
            peak_ratio=ratio,
            settings=settings,
            candidate=candidate,
            packet_stats=packet_stats,
            vmaf_delta=abs(candidate.vmaf_mean - uncapped.vmaf_mean),
            quality_preserved=quality_is_preserved(
                candidate,
                uncapped,
                thresholds,
                max_vmaf_delta=max_vmaf_delta,
            ),
            bitrate_beneficial=(
                packet_stats.average_bitrate_bps
                < uncapped_stats.average_bitrate_bps
                and packet_stats.peak_window_bitrate_bps
                <= uncapped_stats.peak_window_bitrate_bps
            ),
        )
        trials.append(trial)
        print(
            f"[VBV {ratio:.2f}x] VMAF={candidate.vmaf_mean:.3f}，"
            f"P5={candidate.vmaf_p5:.3f}，SSIM={candidate.ssim:.6f}，"
            f"画质{'通过' if trial.quality_preserved else '未通过'}，"
            f"码率{'改善' if trial.bitrate_beneficial else '未改善'}",
            flush=True,
        )
        if trial.quality_preserved and trial.bitrate_beneficial:
            break

    selected = first_quality_preserving_trial(trials)
    return write_rate_control_reports(
        output_dir=output_dir,
        source=input_source,
        reference=reference,
        mode=policy.to_dict(),
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        search=search,
        uncapped=uncapped,
        uncapped_stats=uncapped_stats,
        trials=trials,
        selected=selected,
    )
