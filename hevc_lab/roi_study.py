import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Optional, Tuple

from .adapters import prepare_reference, probe_video
from .adapters.reference import sha256_file
from .core.configs import (
    default_aq_profile,
    resolve_encoder_conditions,
    v1_comparison_plan,
)
from .core.matching import match_equal_quality_candidates
from .core.models import CandidateResult, ROIRegion, Toolchain
from .core.roi import (
    ROIRegionQuality,
    RegionQualityMetrics,
    compare_region_quality,
    decide_roi_selection,
    important_regions,
    load_roi_settings,
)
from .core.search import QualityThresholds
from .metrics import compute_quality
from .quality_search import run_scheme_quality_search
from .reports import render_roi_overlay, write_roi_study_reports


REGION_METRIC_CACHE_VERSION = 1


def _region_metric_cache_key(
    candidate_sha256: str,
    reference_cache_key: str,
    region: ROIRegion,
    vmaf_model_sha256: str,
) -> str:
    basis = {
        "schema_version": REGION_METRIC_CACHE_VERSION,
        "candidate_sha256": candidate_sha256,
        "reference_cache_key": reference_cache_key,
        "region": region.to_dict(),
        "vmaf_model_sha256": vmaf_model_sha256,
    }
    serialized = json.dumps(basis, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _load_or_compute_region_metrics(
    toolchain: Toolchain,
    candidate: CandidateResult,
    candidate_sha256: str,
    reference_path: Path,
    reference_cache_key: str,
    vmaf_model_sha256: str,
    region: ROIRegion,
    metric_root: Path,
) -> RegionQualityMetrics:
    cache_key = _region_metric_cache_key(
        candidate_sha256,
        reference_cache_key,
        region,
        vmaf_model_sha256,
    )
    manifest_path = metric_root / f"{cache_key}.json"
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if payload.get("cache_key") == cache_key:
                stored = payload["metrics"]
                return RegionQualityMetrics(
                    vmaf_mean=float(stored["vmaf_mean"]),
                    vmaf_p5=float(stored["vmaf_p5"]),
                    ssim=float(stored["ssim"]),
                    cache_hit=True,
                )
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            pass
    log_dir = metric_root / "logs" / cache_key
    vmaf_mean, vmaf_p5, ssim = compute_quality(
        toolchain,
        Path(candidate.output_path),
        reference_path,
        log_dir,
        crop=(region.x, region.y, region.width, region.height),
    )
    metrics = RegionQualityMetrics(vmaf_mean, vmaf_p5, ssim)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": REGION_METRIC_CACHE_VERSION,
                "cache_key": cache_key,
                "candidate": candidate.output_path,
                "region": region.to_dict(),
                "metrics": metrics.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return metrics


def evaluate_important_regions(
    toolchain: Toolchain,
    control: CandidateResult,
    roi: CandidateResult,
    reference_path: Path,
    reference_cache_key: str,
    regions: Tuple[ROIRegion, ...],
    output_dir: Path,
) -> Tuple[ROIRegionQuality, ...]:
    metric_root = output_dir / "work" / "roi_metrics"
    metric_root.mkdir(parents=True, exist_ok=True)
    control_sha256 = sha256_file(Path(control.output_path))
    roi_sha256 = sha256_file(Path(roi.output_path))
    model_sha256 = sha256_file(toolchain.vmaf_model)
    comparisons = []
    for region in regions:
        print(f"[{region.region_id}] 正在复算对照局部画质……", flush=True)
        control_metrics = _load_or_compute_region_metrics(
            toolchain,
            control,
            control_sha256,
            reference_path,
            reference_cache_key,
            model_sha256,
            region,
            metric_root,
        )
        print(f"[{region.region_id}] 正在复算 ROI 局部画质……", flush=True)
        roi_metrics = _load_or_compute_region_metrics(
            toolchain,
            roi,
            roi_sha256,
            reference_path,
            reference_cache_key,
            model_sha256,
            region,
            metric_root,
        )
        comparisons.append(compare_region_quality(region, control_metrics, roi_metrics))
    return tuple(comparisons)


def run_roi_study(
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
        raise ValueError("ROI 研究方案只能是 baseline 或 optimized")
    scheme = schemes[scheme_name]
    conditions = resolve_encoder_conditions(plan.conditions, preset)
    thresholds = QualityThresholds(
        vmaf_mean=policy.target_vmaf if target_vmaf is None else target_vmaf,
        vmaf_p5=policy.target_vmaf_p5 if target_vmaf_p5 is None else target_vmaf_p5,
        ssim=policy.target_ssim if target_ssim is None else target_ssim,
    )
    min_speed_x = policy.min_speed_x if min_speed is None else min_speed
    input_source = probe_video(toolchain.ffprobe, input_path)
    settings = load_roi_settings(roi_config_path, mode)
    settings.validate_input(input_source.width, input_source.height)
    output_dir = output_dir.expanduser().resolve()
    reference = prepare_reference(
        toolchain=toolchain,
        input_path=input_path,
        output_dir=output_dir,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        source=input_source,
    )
    settings.validate_input(reference.video.width, reference.video.height)

    aq_profile = default_aq_profile()
    control_scheme = replace(
        scheme,
        name=f"{scheme.name}_roi_control",
        title=f"{scheme.title} / 无 ROI AQ2 对照",
        description="保持默认 AQ2、strength 1.0、qg-size 32，不附加 ROI。",
    )
    roi_scheme = replace(
        scheme,
        name=f"{scheme.name}_roi",
        title=f"{scheme.title} / 静态 ROI",
        description=f"应用摄像头 {settings.camera_id} 的静态 ROI 配置。",
    )
    print("正在独立搜索无 ROI AQ2 对照的画质边界……", flush=True)
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
    print("正在独立搜索静态 ROI 的画质边界……", flush=True)
    roi_search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=reference,
        output_dir=output_dir,
        scheme=roi_scheme,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=min_speed_x,
        adaptive_quantization=aq_profile,
        roi_settings=settings,
    )
    match = match_equal_quality_candidates(
        control_search,
        roi_search,
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
            important_regions(settings),
            output_dir,
        )
    decision = decide_roi_selection(match, region_quality)
    overlay_path = output_dir / "roi_overlay.png"
    render_roi_overlay(toolchain, reference.video.path, settings, overlay_path)
    return write_roi_study_reports(
        output_dir=output_dir,
        source=input_source,
        reference=reference,
        mode=policy.to_dict(),
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        aq_profile=aq_profile,
        settings=settings,
        control_search=control_search,
        roi_search=roi_search,
        match=match,
        region_quality=region_quality,
        decision=decision,
        overlay_path=overlay_path,
    )
