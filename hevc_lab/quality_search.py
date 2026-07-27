import hashlib
import json
from pathlib import Path
from typing import Optional

from .adapters import prepare_reference, probe_video
from .adapters.reference import sha256_file
from .core.configs import resolve_encoder_conditions, v1_comparison_plan
from .core.models import (
    AdaptiveQuantizationSettings,
    CandidateResult,
    DenoiseSettings,
    EncoderConditions,
    InterConfig,
    ReferenceArtifact,
    RateControlSettings,
    ROISettings,
    Toolchain,
    VideoInfo,
)
from .core.search import (
    QualitySearchResult,
    QualitySearchSpec,
    QualityThresholds,
    adaptive_quality_search,
)
from .core.selection import calculate_saving
from .core.speed import classify_speed, speed_gate_passes
from .encoders import build_x265_params, encode_candidate
from .metrics import compute_quality
from .reports import write_quality_search_reports


SEARCH_CACHE_SCHEMA_VERSION = 7
SEARCH_EVALUATOR_VERSION = "x265-vmaf-budget-neutral-roi-v1.4"


def build_candidate_cache_key(
    input_sha256: str,
    reference_cache_key: str,
    config: InterConfig,
    conditions: EncoderConditions,
    fps: float,
    crf: float,
    vmaf_model_sha256: str,
    min_speed_x: Optional[float] = 0.97,
    rate_control: Optional[RateControlSettings] = None,
    adaptive_quantization: Optional[AdaptiveQuantizationSettings] = None,
    roi_settings: Optional[ROISettings] = None,
    denoise_settings: Optional[DenoiseSettings] = None,
) -> str:
    basis = {
        "schema_version": SEARCH_CACHE_SCHEMA_VERSION,
        "evaluator_version": SEARCH_EVALUATOR_VERSION,
        "input_sha256": input_sha256,
        "reference_cache_key": reference_cache_key,
        "config": config.to_dict(fps),
        "conditions": conditions.cache_identity(),
        "crf": round(crf, 8),
        "vmaf_model_sha256": vmaf_model_sha256,
        "speed_policy": {
            "speed_gate_enabled": min_speed_x is not None,
            "min_speed_x": min_speed_x,
        },
        "rate_control": rate_control.to_dict() if rate_control else None,
    }
    if adaptive_quantization:
        basis["adaptive_quantization"] = adaptive_quantization.to_dict()
    if roi_settings:
        basis["roi"] = roi_settings.cache_identity()
    if denoise_settings:
        basis["denoise"] = denoise_settings.cache_identity()
    serialized = json.dumps(basis, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _crf_tag(crf: float) -> str:
    return f"crf_{crf:04.1f}".replace(".", "_")


def _load_cached_candidate(
    manifest_path: Path,
    candidate_path: Path,
    cache_key: str,
    thresholds: QualityThresholds,
    min_speed_x: Optional[float],
) -> Optional[CandidateResult]:
    if not manifest_path.is_file() or not candidate_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("cache_key") != cache_key:
            return None
        stored = payload["candidate"]
        if candidate_path.stat().st_size != int(stored["file_size_bytes"]):
            return None
        stored["output_path"] = str(candidate_path)
        stored["cache_hit"] = True
        candidate = CandidateResult(**stored)
        candidate.quality_pass = thresholds.accepts(candidate)
        candidate.speed_pass = speed_gate_passes(
            candidate.encode_speed_x,
            min_speed_x,
        )
        candidate.speed_tier = classify_speed(
            candidate.encode_speed_x,
            min_speed_x,
        )
        candidate.eligible = candidate.quality_pass and candidate.speed_pass
        return candidate
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_candidate_manifest(
    manifest_path: Path,
    cache_key: str,
    reference: ReferenceArtifact,
    config: InterConfig,
    conditions: EncoderConditions,
    candidate: CandidateResult,
    rate_control: Optional[RateControlSettings] = None,
    adaptive_quantization: Optional[AdaptiveQuantizationSettings] = None,
    roi_settings: Optional[ROISettings] = None,
    denoise_settings: Optional[DenoiseSettings] = None,
    min_speed_x: Optional[float] = 0.97,
) -> None:
    payload = {
        "schema_version": SEARCH_CACHE_SCHEMA_VERSION,
        "cache_key": cache_key,
        "input_sha256": reference.input_sha256,
        "reference_cache_key": reference.cache_key,
        "config": config.to_dict(reference.video.fps),
        "conditions": conditions.to_dict(),
        "speed_policy": {
            "speed_gate_enabled": min_speed_x is not None,
            "min_speed_x": min_speed_x,
        },
        "rate_control": rate_control.to_dict() if rate_control else None,
        "adaptive_quantization": (
            adaptive_quantization.to_dict() if adaptive_quantization else None
        ),
        "roi": roi_settings.to_dict() if roi_settings else None,
        "denoise": denoise_settings.to_dict() if denoise_settings else None,
        "candidate": candidate.to_dict(),
    }
    temporary = manifest_path.with_suffix(".part.json")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(manifest_path)


def evaluate_scheme_crf(
    toolchain: Toolchain,
    input_source: VideoInfo,
    reference: ReferenceArtifact,
    output_dir: Path,
    scheme: InterConfig,
    conditions: EncoderConditions,
    thresholds: QualityThresholds,
    min_speed_x: Optional[float],
    crf: float,
    rate_control: Optional[RateControlSettings] = None,
    adaptive_quantization: Optional[AdaptiveQuantizationSettings] = None,
    roi_settings: Optional[ROISettings] = None,
    denoise_settings: Optional[DenoiseSettings] = None,
) -> CandidateResult:
    """评估并缓存一个确定 CRF 的候选，供短片搜索和完整片回退复用。"""
    candidate_dir = output_dir / scheme.name / "candidates"
    log_root = output_dir / scheme.name / "logs"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)
    vmaf_model_sha256 = sha256_file(toolchain.vmaf_model)
    tag = _crf_tag(crf)
    candidate_path = (candidate_dir / f"{tag}.mp4").resolve()
    manifest_path = candidate_dir / f"{tag}.json"
    cache_key = build_candidate_cache_key(
        input_sha256=reference.input_sha256,
        reference_cache_key=reference.cache_key,
        config=scheme,
        conditions=conditions,
        fps=reference.video.fps,
        crf=crf,
        vmaf_model_sha256=vmaf_model_sha256,
        min_speed_x=min_speed_x,
        rate_control=rate_control,
        adaptive_quantization=adaptive_quantization,
        roi_settings=roi_settings,
        denoise_settings=denoise_settings,
    )
    cached = _load_cached_candidate(
        manifest_path,
        candidate_path,
        cache_key,
        thresholds,
        min_speed_x,
    )
    if cached:
        print(
            f"[{scheme.name}] CRF {crf:.1f}：缓存命中，"
            f"VMAF={cached.vmaf_mean:.3f}，P5={cached.vmaf_p5:.3f}",
            flush=True,
        )
        return cached

    log_dir = log_root / tag
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{scheme.name}] CRF {crf:.1f}：正在编码……", flush=True)
    timing = encode_candidate(
        toolchain=toolchain,
        source=reference.video,
        config=scheme,
        destination=candidate_path,
        log_path=log_dir / "encode.log",
        crf=crf,
        conditions=conditions,
        rate_control=rate_control,
        adaptive_quantization=adaptive_quantization,
        roi_settings=roi_settings,
        denoise_settings=denoise_settings,
    )
    encoded = probe_video(toolchain.ffprobe, candidate_path)
    print(f"[{scheme.name}] CRF {crf:.1f}：正在计算 VMAF/SSIM……", flush=True)
    vmaf_mean, vmaf_p5, ssim = compute_quality(
        toolchain,
        candidate_path,
        reference.video.path,
        log_dir,
    )
    speed_pass = speed_gate_passes(timing["speed"], min_speed_x)
    candidate = CandidateResult(
        name=scheme.name,
        title=scheme.title,
        description=scheme.description,
        output_path=str(candidate_path),
        x265_params=build_x265_params(
            scheme,
            reference.video.fps,
            rate_control,
            adaptive_quantization,
        ),
        crf=crf,
        preset=conditions.preset,
        bitrate_bps=encoded.video_bitrate_bps,
        file_size_bytes=encoded.file_size_bytes,
        vmaf_mean=vmaf_mean,
        vmaf_p5=vmaf_p5,
        ssim=ssim,
        encode_seconds=timing["elapsed"],
        encode_speed_x=timing["speed"],
        quality_pass=False,
        speed_pass=speed_pass,
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
        rate_control,
        adaptive_quantization,
        roi_settings,
        denoise_settings,
        min_speed_x=min_speed_x,
    )
    print(
        f"[{scheme.name}] CRF {crf:.1f}：VMAF={vmaf_mean:.3f}，"
        f"P5={vmaf_p5:.3f}，SSIM={ssim:.6f}，"
        f"{'合格' if candidate.quality_pass else '不合格'}",
        flush=True,
    )
    return candidate


def run_scheme_quality_search(
    toolchain: Toolchain,
    input_source: VideoInfo,
    reference: ReferenceArtifact,
    output_dir: Path,
    scheme: InterConfig,
    conditions: EncoderConditions,
    thresholds: QualityThresholds,
    min_speed_x: Optional[float],
    rate_control: Optional[RateControlSettings] = None,
    adaptive_quantization: Optional[AdaptiveQuantizationSettings] = None,
    roi_settings: Optional[ROISettings] = None,
    denoise_settings: Optional[DenoiseSettings] = None,
    crf_min: float = 18.0,
    crf_max: float = 38.0,
    crf_step: float = 0.5,
) -> QualitySearchResult:
    """在已准备好的同一参考片段上搜索一个编码方案。"""
    spec = QualitySearchSpec(
        thresholds=thresholds,
        crf_min=crf_min,
        crf_max=crf_max,
        crf_step=crf_step,
        anchors=(crf_min, 28.0, crf_max) if crf_min <= 28.0 <= crf_max else (crf_min, crf_max),
    )
    def evaluate(crf: float) -> CandidateResult:
        return evaluate_scheme_crf(
            toolchain=toolchain,
            input_source=input_source,
            reference=reference,
            output_dir=output_dir,
            scheme=scheme,
            conditions=conditions,
            thresholds=thresholds,
            min_speed_x=min_speed_x,
            crf=crf,
            rate_control=rate_control,
            adaptive_quantization=adaptive_quantization,
            roi_settings=roi_settings,
            denoise_settings=denoise_settings,
        )

    return adaptive_quality_search(evaluate, spec)


def run_single_quality_search(
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
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
) -> dict:
    plan = v1_comparison_plan(mode)
    policy = plan.mode
    schemes = {config.name: config for config in plan.schemes}
    if scheme_name not in schemes:
        raise ValueError("搜索方案只能是 baseline 或 optimized")
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
    search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
        crf_min=policy.crf_search_min,
        crf_max=policy.crf_search_max,
        crf_step=policy.crf_search_step,
    )
    return write_quality_search_reports(
        output_dir=output_dir,
        source=input_source,
        reference=reference,
        mode=policy.to_dict(),
        scheme=scheme,
        conditions=conditions,
        search=search,
    )
