import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .adapters import prepare_reference, probe_video
from .adapters.reference import sha256_file
from .core.configs import (
    default_aq_profile,
    denoise_policy_for_mode,
    multi_encode_strategies,
    v1_comparison_plan,
)
from .core.models import (
    CandidateResult,
    DenoiseSettings,
    MultiEncodeStrategy,
    ReferenceArtifact,
    Toolchain,
)
from .core.roi import load_roi_settings
from .core.search import QualityThresholds
from .encoders import encode_default_h264, encode_default_x265
from .quality_search import evaluate_scheme_crf, run_scheme_quality_search
from .reports import write_multi_encode_reports
from .roi_study import evaluate_important_regions
from .core.roi import important_regions


MULTI_ENCODE_SCHEMA_VERSION = 6
MULTI_ENCODE_PIPELINE_VERSION = "v2.0.0"
V1_6_HEVC_FIXED_CRF = 36.0
SHORT_SEARCH_SECONDS = 12.0
CRF_MIN = 18.0
CRF_STEP = 0.5
ProgressCallback = Callable[[str], None]


def _notify_progress(
    progress_callback: Optional[ProgressCallback],
    stage: str,
) -> None:
    """报告观察性进度；回调异常不得影响编码结果。"""
    if progress_callback is None:
        return
    try:
        progress_callback(stage)
    except Exception:
        # Web 进度属于旁路信息，不能改变正式编码流程和结果。
        pass


def bitrate_saving_vs_default_pct(
    default_bitrate_bps: float,
    strategy_bitrate_bps: float,
) -> float:
    if default_bitrate_bps <= 0:
        raise ValueError("默认方案平均视频包码率必须大于 0")
    return (
        (default_bitrate_bps - strategy_bitrate_bps)
        / default_bitrate_bps
        * 100.0
    )


def _mode_target_range(policy) -> Tuple[Optional[float], Optional[float]]:
    return policy.target_saving_min_pct, policy.target_saving_max_pct


def _saving_target_status(
    saving_pct: Optional[float],
    target_min_pct: Optional[float],
    target_max_pct: Optional[float],
) -> Tuple[Optional[bool], str]:
    if target_min_pct is None or target_max_pct is None:
        return None, "not_applicable"
    if saving_pct is None:
        return False, "not_generated"
    if target_min_pct <= saving_pct <= target_max_pct:
        return True, "met"
    if saving_pct < target_min_pct:
        return False, "below_target"
    return False, "above_target"


def _candidate_from_strategy_result(result: Optional[dict]) -> Optional[CandidateResult]:
    if not result or result.get("status") != "completed":
        return None
    payload = result.get("selected_candidate")
    if not isinstance(payload, dict):
        return None
    try:
        return CandidateResult(**payload)
    except TypeError:
        return None


def _format_budget_mbps(value: Optional[float]) -> str:
    if value is None:
        return "未知"
    return f"{value / 1_000_000.0:.6f} Mbit/s"


def _summarize_roi_region_quality(region_quality) -> dict:
    rows = []
    failure_reasons = []
    improved = False
    preserved = True
    for item in region_quality:
        vmaf_delta = item.roi.vmaf_mean - item.control.vmaf_mean
        p5_delta = item.roi.vmaf_p5 - item.control.vmaf_p5
        ssim_delta = item.roi.ssim - item.control.ssim
        region_preserved = (
            vmaf_delta >= -1e-9
            and p5_delta >= -1e-9
            and ssim_delta >= -1e-9
        )
        region_improved = (
            vmaf_delta > 1e-9
            or p5_delta > 1e-9
            or ssim_delta > 1e-9
        )
        if not region_preserved:
            preserved = False
            failure_reasons.append(
                f"{item.region.title} 局部质量低于通用无 ROI 方案"
            )
        improved = improved or region_improved
        rows.append(
            {
                **item.to_dict(),
                "vmaf_delta_vs_general": vmaf_delta,
                "vmaf_p5_delta_vs_general": p5_delta,
                "ssim_delta_vs_general": ssim_delta,
                "quality_preserved_vs_general": region_preserved,
                "quality_improved_vs_general": region_improved,
            }
        )
    if not rows:
        preserved = False
        failure_reasons.append("ROI 配置没有 critical/evidence 重点区域")
    return {
        "roi_quality_preserved": preserved,
        "roi_quality_improved": improved,
        "roi_region_quality": rows,
        "roi_quality_failure_reasons": failure_reasons,
    }


def _point_saving(default_bitrate_bps: float, point: CandidateResult) -> float:
    return bitrate_saving_vs_default_pct(default_bitrate_bps, point.bitrate_bps)


def _dedupe_points(points: List[CandidateResult]) -> List[CandidateResult]:
    by_crf = {round(point.crf, 8): point for point in points}
    return [by_crf[key] for key in sorted(by_crf)]


def _crf_grid(
    crf_min: float = CRF_MIN,
    crf_max: float = 38.0,
    crf_step: float = CRF_STEP,
) -> Tuple[float, ...]:
    count = round((crf_max - crf_min) / crf_step)
    return tuple(round(crf_min + index * crf_step, 8) for index in range(count + 1))


def _target_distance(
    saving_pct: float,
    target_min_pct: float,
    target_max_pct: float,
) -> float:
    if saving_pct < target_min_pct:
        return target_min_pct - saving_pct
    if saving_pct > target_max_pct:
        return saving_pct - target_max_pct
    return 0.0


def _select_short_candidate(
    points: List[CandidateResult],
    default_bitrate_bps: float,
    target_min_pct: Optional[float],
    target_max_pct: Optional[float],
) -> Tuple[Optional[CandidateResult], dict]:
    """从短片候选中选择完整片起始 CRF；有目标区间时优先命中目标。"""
    ordered = _dedupe_points(points)
    rows = []
    for point in ordered:
        saving = (
            _point_saving(default_bitrate_bps, point)
            if default_bitrate_bps > 0
            else None
        )
        rows.append(
            {
                "crf": point.crf,
                "quality_pass": point.quality_pass,
                "bitrate_bps": point.bitrate_bps,
                "saving_vs_default_pct": saving,
                "vmaf_mean": point.vmaf_mean,
                "vmaf_p5": point.vmaf_p5,
                "ssim": point.ssim,
                "cache_hit": point.cache_hit,
            }
        )
    passing = [point for point in ordered if point.quality_pass]
    if not passing:
        return None, {
            "target_saving_min_pct": target_min_pct,
            "target_saving_max_pct": target_max_pct,
            "target_saving_met": False if target_min_pct is not None else None,
            "saving_target_status": "no_quality_candidate",
            "selection_reason": "no_quality_passing_short_candidate",
            "selected_crf": None,
            "selected_short_saving_vs_default_pct": None,
            "points": rows,
        }
    if target_min_pct is None or target_max_pct is None:
        selected = max(passing, key=lambda item: item.crf)
        return selected, {
            "target_saving_min_pct": None,
            "target_saving_max_pct": None,
            "target_saving_met": None,
            "saving_target_status": "not_applicable",
            "selection_reason": "highest_quality_passing_crf",
            "selected_crf": selected.crf,
            "selected_short_saving_vs_default_pct": _point_saving(
                default_bitrate_bps,
                selected,
            ),
            "points": rows,
        }

    with_saving = [
        (point, _point_saving(default_bitrate_bps, point))
        for point in passing
    ]
    in_range = [
        (point, saving)
        for point, saving in with_saving
        if target_min_pct <= saving <= target_max_pct
    ]
    if in_range:
        selected, selected_saving = max(
            in_range,
            key=lambda item: (item[1], item[0].crf),
        )
        return selected, {
            "target_saving_min_pct": target_min_pct,
            "target_saving_max_pct": target_max_pct,
            "target_saving_met": True,
            "saving_target_status": "met",
            "selection_reason": "target_range_lowest_bitrate_quality_pass",
            "selected_crf": selected.crf,
            "selected_short_saving_vs_default_pct": selected_saving,
            "points": rows,
        }

    selected, selected_saving = min(
        with_saving,
        key=lambda item: (
            _target_distance(item[1], target_min_pct, target_max_pct),
            -item[1],
            -item[0].crf,
        ),
    )
    _, status = _saving_target_status(
        selected_saving,
        target_min_pct,
        target_max_pct,
    )
    if status == "below_target":
        reason = "no_target_range_candidate_selected_highest_saving_quality_pass"
    elif status == "above_target":
        reason = "no_target_range_candidate_selected_closest_over_target_quality_pass"
    else:
        reason = "no_target_range_candidate_selected_closest_quality_pass"
    return selected, {
        "target_saving_min_pct": target_min_pct,
        "target_saving_max_pct": target_max_pct,
        "target_saving_met": False,
        "saving_target_status": status,
        "selection_reason": reason,
        "selected_crf": selected.crf,
        "selected_short_saving_vs_default_pct": selected_saving,
        "points": rows,
    }


def _expand_target_search_points(
    *,
    toolchain: Toolchain,
    input_source,
    reference: ReferenceArtifact,
    output_dir: Path,
    scheme,
    conditions,
    thresholds: QualityThresholds,
    default_bitrate_bps: float,
    target_min_pct: Optional[float],
    target_max_pct: Optional[float],
    initial_points: List[CandidateResult],
    adaptive_quantization,
    roi_settings,
    denoise_settings,
    crf_min: float = CRF_MIN,
    crf_max: float = 38.0,
    crf_step: float = CRF_STEP,
) -> List[CandidateResult]:
    """围绕目标节省区间补测短片点；V1.4 正式策略无目标区间时直接返回。"""
    if target_min_pct is None or target_max_pct is None:
        return initial_points
    points = {round(point.crf, 8): point for point in initial_points}
    passing = [point for point in points.values() if point.quality_pass]
    if not passing:
        return list(points.values())
    selected = max(passing, key=lambda item: item.crf)
    grid = _crf_grid(crf_min, crf_max, crf_step)
    try:
        selected_index = grid.index(round(selected.crf, 8))
    except ValueError:
        return list(points.values())

    def evaluate_grid_index(index: int) -> CandidateResult:
        crf = grid[index]
        cached = points.get(crf)
        if cached is not None:
            return cached
        candidate = evaluate_scheme_crf(
            toolchain=toolchain,
            input_source=input_source,
            reference=reference,
            output_dir=output_dir,
            scheme=scheme,
            conditions=conditions,
            thresholds=thresholds,
            min_speed_x=None,
            crf=crf,
            adaptive_quantization=adaptive_quantization,
            roi_settings=roi_settings,
            denoise_settings=denoise_settings,
        )
        points[crf] = candidate
        return candidate

    selected_saving = _point_saving(default_bitrate_bps, selected)
    if selected_saving < target_min_pct:
        for index in range(selected_index + 1, len(grid)):
            candidate = evaluate_grid_index(index)
            if not candidate.quality_pass:
                break
            if _point_saving(default_bitrate_bps, candidate) > target_max_pct:
                break
    elif selected_saving > target_max_pct:
        for index in range(selected_index - 1, -1, -1):
            candidate = evaluate_grid_index(index)
            if (
                candidate.quality_pass
                and _point_saving(default_bitrate_bps, candidate) < target_min_pct
            ):
                break
    else:
        next_crf = (
            grid[selected_index + 1]
            if selected_index + 1 < len(grid)
            else None
        )
        next_point = points.get(next_crf) if next_crf is not None else None
        if next_point is not None and next_point.quality_pass:
            for index in range(selected_index + 1, len(grid)):
                candidate = evaluate_grid_index(index)
                if not candidate.quality_pass:
                    break
                if _point_saving(default_bitrate_bps, candidate) > target_max_pct:
                    break
    return list(points.values())


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".part.json")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _default_cache_key(reference: ReferenceArtifact, toolchain: Toolchain) -> str:
    executable = toolchain.ffmpeg.resolve()
    stat = executable.stat()
    basis = {
        "schema_version": MULTI_ENCODE_SCHEMA_VERSION,
        "pipeline_version": MULTI_ENCODE_PIPELINE_VERSION,
        "input_sha256": reference.input_sha256,
        "reference_cache_key": reference.cache_key,
        "encoder": "libx265",
        "custom_crf": None,
        "custom_preset": None,
        "custom_x265_params": None,
        "custom_filters": None,
        "ffmpeg": {
            "path": str(executable),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
    }
    serialized = json.dumps(basis, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _native_h264_cache_key(reference: ReferenceArtifact, toolchain: Toolchain) -> str:
    executable = toolchain.ffmpeg.resolve()
    stat = executable.stat()
    basis = {
        "schema_version": MULTI_ENCODE_SCHEMA_VERSION,
        "pipeline_version": MULTI_ENCODE_PIPELINE_VERSION,
        "input_sha256": reference.input_sha256,
        "reference_cache_key": reference.cache_key,
        "encoder": "libx264",
        "custom_crf": None,
        "custom_preset": None,
        "custom_x264_params": None,
        "custom_filters": None,
        "ffmpeg": {
            "path": str(executable),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
    }
    serialized = json.dumps(basis, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _default_strategy(
    toolchain: Toolchain,
    reference: ReferenceArtifact,
    output_dir: Path,
) -> dict:
    destination = (output_dir / "default_x265.mp4").resolve()
    cache_dir = output_dir / "work" / "default"
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "default_x265.json"
    log_path = cache_dir / "encode.log"
    cache_key = _default_cache_key(reference, toolchain)
    cache_hit = False
    timing = {"elapsed": 0.0, "speed": 0.0}
    if manifest_path.is_file() and destination.is_file():
        try:
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                stored.get("cache_key") == cache_key
                and destination.stat().st_size
                == int(stored["output"]["file_size_bytes"])
            ):
                timing = stored["timing"]
                cache_hit = True
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            cache_hit = False
    if not cache_hit:
        print("[default] 正在使用 libx265 原生默认参数编码完整视频……", flush=True)
        timing = encode_default_x265(
            toolchain=toolchain,
            source=reference.video,
            destination=destination,
            log_path=log_path,
        )
    output = probe_video(toolchain.ffprobe, destination)
    payload = {
        "strategy_id": "default_x265",
        "title": "x265原生默认",
        "mode": None,
        "public_mode": "default",
        "source_mode": "x265_native_default",
        "strategy_generation": "x265_native_default",
        "region_processing_enabled": False,
        "roi_enabled": False,
        "denoise_enabled": False,
        "effective_preset": None,
        "crf_search_max": None,
        "budget_reference_strategy_id": None,
        "budget_neutral_required": False,
        "roi_quality_required": False,
        "experimental": False,
        "status": "completed",
        "output_path": str(destination),
        "width": output.width,
        "height": output.height,
        "resolution": f"{output.width}x{output.height}",
        "average_video_packet_bitrate_bps": output.video_bitrate_bps,
        "average_video_packet_bitrate_mbps": output.video_bitrate_bps / 1_000_000.0,
        "saving_vs_default_pct": None,
        "saving_vs_general_no_roi_pct": None,
        "budget_bitrate_bps": None,
        "budget_bitrate_mbps": None,
        "budget_neutral_pass": None,
        "budget_margin_bps": None,
        "roi_quality_preserved": None,
        "roi_quality_improved": None,
        "roi_region_quality": [],
        "target_saving_min_pct": None,
        "target_saving_max_pct": None,
        "target_saving_met": None,
        "saving_target_status": "not_applicable",
        "selection_reason": "default_x265_reference",
        "cache_hit": cache_hit,
        "timing": timing,
        "encoder": {
            "name": "libx265",
            "custom_crf": None,
            "custom_preset": None,
            "custom_x265_params": None,
            "custom_filters": None,
            "description": "只指定 libx265，使用 FFmpeg/libx265 原生默认参数",
        },
    }
    _atomic_json(
        manifest_path,
        {
            "schema_version": MULTI_ENCODE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "timing": timing,
            "output": {
                "path": str(destination),
                "file_size_bytes": output.file_size_bytes,
                "video": output.to_dict(),
            },
        },
    )
    return payload


def _h264_native_strategy(
    toolchain: Toolchain,
    reference: ReferenceArtifact,
    output_dir: Path,
) -> dict:
    destination = (output_dir / "default_h264.mp4").resolve()
    cache_dir = output_dir / "work" / "h264_native"
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "default_h264.json"
    log_path = cache_dir / "encode.log"
    cache_key = _native_h264_cache_key(reference, toolchain)
    cache_hit = False
    timing = {"elapsed": 0.0, "speed": 0.0}
    if manifest_path.is_file() and destination.is_file():
        try:
            stored = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                stored.get("cache_key") == cache_key
                and destination.stat().st_size
                == int(stored["output"]["file_size_bytes"])
            ):
                timing = stored["timing"]
                cache_hit = True
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            cache_hit = False
    if not cache_hit:
        print("[h264_native] 正在使用 libx264 原生默认参数编码完整视频……", flush=True)
        timing = encode_default_h264(
            toolchain=toolchain,
            source=reference.video,
            destination=destination,
            log_path=log_path,
        )
    output = probe_video(toolchain.ffprobe, destination)
    payload = {
        "strategy_id": "default_h264",
        "title": "H.264 原生编码",
        "mode": "h264_native",
        "public_mode": "h264_native",
        "source_mode": "h264_native_default",
        "strategy_generation": "v1.6_h264_native_default",
        "region_processing_enabled": False,
        "roi_enabled": False,
        "denoise_enabled": False,
        "effective_preset": None,
        "crf_search_max": None,
        "budget_reference_strategy_id": None,
        "budget_neutral_required": False,
        "roi_quality_required": False,
        "experimental": False,
        "status": "completed",
        "output_path": str(destination),
        "width": output.width,
        "height": output.height,
        "resolution": f"{output.width}x{output.height}",
        "average_video_packet_bitrate_bps": output.video_bitrate_bps,
        "average_video_packet_bitrate_mbps": output.video_bitrate_bps / 1_000_000.0,
        "saving_vs_default_pct": None,
        "saving_vs_general_no_roi_pct": None,
        "budget_bitrate_bps": None,
        "budget_bitrate_mbps": None,
        "budget_neutral_pass": None,
        "budget_margin_bps": None,
        "roi_quality_preserved": None,
        "roi_quality_improved": None,
        "roi_region_quality": [],
        "target_saving_min_pct": None,
        "target_saving_max_pct": None,
        "target_saving_met": None,
        "saving_target_status": "not_applicable",
        "selection_reason": "h264_native_reference",
        "cache_hit": cache_hit,
        "timing": timing,
        "encoder": {
            "name": "libx264",
            "custom_crf": None,
            "custom_preset": None,
            "custom_x264_params": None,
            "custom_filters": None,
            "description": "只指定 libx264，使用 FFmpeg/libx264 原生默认参数",
        },
    }
    _atomic_json(
        manifest_path,
        {
            "schema_version": MULTI_ENCODE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "timing": timing,
            "output": {
                "path": str(destination),
                "file_size_bytes": output.file_size_bytes,
                "video": output.to_dict(),
            },
        },
    )
    return payload


def _publish_candidate(candidate: CandidateResult, destination: Path) -> bool:
    source = Path(candidate.output_path).resolve()
    if destination.is_file():
        try:
            if (
                destination.stat().st_size == source.stat().st_size
                and sha256_file(destination) == sha256_file(source)
            ):
                return True
        except OSError:
            pass
    shutil.copy2(source, destination)
    return False


def _failed_strategy_from_exception(
    strategy: MultiEncodeStrategy,
    reason: str,
    fps: float,
) -> dict:
    return {
        "strategy_id": strategy.strategy_id,
        "title": strategy.title,
        "mode": strategy.public_mode,
        "public_mode": strategy.public_mode,
        "source_mode": strategy.source_mode,
        "strategy_generation": strategy.strategy_generation,
        "region_processing_enabled": strategy.region_processing_enabled,
        "roi_enabled": strategy.roi_enabled,
        "denoise_enabled": strategy.denoise_enabled,
        "effective_preset": strategy.effective_preset,
        "crf_search_max": strategy.crf_search_max,
        "budget_reference_strategy_id": strategy.budget_reference,
        "budget_neutral_required": strategy.budget_neutral_required,
        "roi_quality_required": strategy.roi_quality_required,
        "experimental": strategy.experimental,
        "strategy": strategy.to_dict(),
        "status": "failed",
        "failure_reason": reason,
        "output_path": None,
        "width": None,
        "height": None,
        "resolution": None,
        "average_video_packet_bitrate_bps": None,
        "average_video_packet_bitrate_mbps": None,
        "saving_vs_default_pct": None,
        "saving_vs_general_no_roi_pct": None,
        "budget_bitrate_bps": None,
        "budget_bitrate_mbps": None,
        "budget_neutral_pass": None,
        "budget_margin_bps": None,
        "roi_quality_preserved": None,
        "roi_quality_improved": None,
        "roi_region_quality": [],
        "target_saving_min_pct": None,
        "target_saving_max_pct": None,
        "target_saving_met": None,
        "saving_target_status": "not_generated",
        "selection_reason": "strategy_setup_failed",
        "cache_hit": False,
        "thresholds": {
            "vmaf_mean": strategy.target_vmaf,
            "vmaf_p5": strategy.target_vmaf_p5,
            "ssim": strategy.target_ssim,
        },
        "short_search": None,
        "target_selection": None,
        "full_attempts": [],
        "config": v1_comparison_plan(strategy.source_mode).optimized.to_dict(fps),
        "conditions": None,
        "adaptive_quantization": default_aq_profile().to_dict(),
        "roi": None,
        "denoise": None,
    }


def _fixed_hevc_strategy(
    toolchain: Toolchain,
    input_source,
    full_reference: ReferenceArtifact,
    output_dir: Path,
    default_bitrate_bps: float,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    strategy = multi_encode_strategies()[0]
    plan = v1_comparison_plan(strategy.source_mode)
    scheme = plan.optimized
    conditions = replace(
        plan.conditions,
        preset=strategy.effective_preset,
        preset_source="v1.6_fixed_strategy",
        mode_default_preset=plan.conditions.mode_default_preset
        or plan.conditions.preset,
    )
    thresholds = QualityThresholds(
        vmaf_mean=strategy.target_vmaf,
        vmaf_p5=strategy.target_vmaf_p5,
        ssim=strategy.target_ssim,
    )
    aq = default_aq_profile()
    _notify_progress(progress_callback, "encoding_hevc_fixed")
    print("[hevc_fixed] 正在按 V1.6 固定参数编码完整视频……", flush=True)
    candidate = evaluate_scheme_crf(
        toolchain=toolchain,
        input_source=input_source,
        reference=full_reference,
        output_dir=output_dir / "work" / "hevc_fixed",
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=None,
        crf=V1_6_HEVC_FIXED_CRF,
        adaptive_quantization=aq,
        roi_settings=None,
        denoise_settings=None,
    )
    destination = (output_dir / strategy.output_filename).resolve()
    publish_cache_hit = _publish_candidate(candidate, destination)
    output = probe_video(toolchain.ffprobe, destination)
    saving_vs_default = bitrate_saving_vs_default_pct(
        default_bitrate_bps,
        output.video_bitrate_bps,
    )
    return {
        "strategy_id": strategy.strategy_id,
        "title": strategy.title,
        "mode": strategy.public_mode,
        "public_mode": strategy.public_mode,
        "source_mode": strategy.source_mode,
        "strategy_generation": strategy.strategy_generation,
        "region_processing_enabled": False,
        "roi_enabled": False,
        "denoise_enabled": False,
        "effective_preset": conditions.preset,
        "crf_search_max": strategy.crf_search_max,
        "budget_reference_strategy_id": None,
        "budget_neutral_required": False,
        "roi_quality_required": False,
        "experimental": False,
        "strategy": strategy.to_dict(),
        "status": "completed",
        "failure_reason": None,
        "output_path": str(destination),
        "width": output.width,
        "height": output.height,
        "resolution": f"{output.width}x{output.height}",
        "average_video_packet_bitrate_bps": output.video_bitrate_bps,
        "average_video_packet_bitrate_mbps": output.video_bitrate_bps / 1_000_000.0,
        "saving_vs_default_pct": saving_vs_default,
        "saving_vs_general_no_roi_pct": None,
        "budget_bitrate_bps": None,
        "budget_bitrate_mbps": None,
        "budget_neutral_pass": None,
        "budget_margin_bps": None,
        "roi_quality_preserved": None,
        "roi_quality_improved": None,
        "roi_region_quality": [],
        "target_saving_min_pct": None,
        "target_saving_max_pct": None,
        "target_saving_met": None,
        "saving_target_status": "not_applicable",
        "selection_reason": "v1.6_fixed_crf_no_search",
        "cache_hit": bool(publish_cache_hit and candidate.cache_hit),
        "selected_crf": candidate.crf,
        "vmaf_mean": candidate.vmaf_mean,
        "vmaf_p5": candidate.vmaf_p5,
        "ssim": candidate.ssim,
        "encode_speed_x": candidate.encode_speed_x,
        "quality_pass": candidate.quality_pass,
        "thresholds": thresholds.to_dict(),
        "short_search": None,
        "target_selection": None,
        "full_attempts": [candidate.to_dict()],
        "selected_candidate": candidate.to_dict(),
        "config": scheme.to_dict(full_reference.video.fps),
        "conditions": conditions.to_dict(),
        "adaptive_quantization": aq.to_dict(),
        "roi": None,
        "denoise": None,
    }


def _composite_strategy(
    toolchain: Toolchain,
    input_source,
    short_reference: ReferenceArtifact,
    full_reference: ReferenceArtifact,
    roi_config_path: Path,
    output_dir: Path,
    strategy: MultiEncodeStrategy,
    default_bitrate_bps: float,
    progress_callback: Optional[ProgressCallback] = None,
    budget_reference: Optional[dict] = None,
) -> dict:
    plan = v1_comparison_plan(strategy.source_mode)
    scheme = plan.optimized
    conditions = replace(
        plan.conditions,
        preset=strategy.effective_preset,
        preset_source="v1.4_public_strategy",
        mode_default_preset=plan.conditions.mode_default_preset
        or plan.conditions.preset,
    )
    thresholds = QualityThresholds(
        vmaf_mean=strategy.target_vmaf,
        vmaf_p5=strategy.target_vmaf_p5,
        ssim=strategy.target_ssim,
    )
    aq = default_aq_profile()
    roi = None
    if strategy.roi_enabled:
        roi = load_roi_settings(roi_config_path, strategy.source_mode)
        roi.validate_input(input_source.width, input_source.height)
    denoise = (
        DenoiseSettings(
            roi=roi,
            policy=denoise_policy_for_mode(strategy.source_mode),
        )
        if strategy.denoise_enabled
        else None
    )
    target_min_pct, target_max_pct = None, None
    budget_candidate = _candidate_from_strategy_result(budget_reference)
    budget_bitrate_bps = (
        None
        if budget_reference is None
        else budget_reference.get("average_video_packet_bitrate_bps")
    )
    strategy_metadata = {
        "public_mode": strategy.public_mode,
        "source_mode": strategy.source_mode,
        "strategy_generation": strategy.strategy_generation,
        "region_processing_enabled": strategy.region_processing_enabled,
        "roi_enabled": strategy.roi_enabled,
        "denoise_enabled": strategy.denoise_enabled,
        "effective_preset": conditions.preset,
        "crf_search_max": strategy.crf_search_max,
        "budget_reference_strategy_id": strategy.budget_reference,
        "budget_neutral_required": strategy.budget_neutral_required,
        "roi_quality_required": strategy.roi_quality_required,
        "experimental": strategy.experimental,
        "budget_bitrate_bps": budget_bitrate_bps,
        "budget_bitrate_mbps": (
            None if budget_bitrate_bps is None else budget_bitrate_bps / 1_000_000.0
        ),
        "budget_neutral_pass": None,
        "budget_margin_bps": None,
        "roi_quality_preserved": None,
        "roi_quality_improved": None,
        "roi_region_quality": [],
        "roi_quality_failure_reasons": [],
        "strategy": strategy.to_dict(),
    }
    if strategy.budget_neutral_required and (
        budget_bitrate_bps is None or budget_candidate is None
    ):
        return {
            **strategy_metadata,
            "strategy_id": strategy.strategy_id,
            "title": strategy.title,
            "mode": strategy.public_mode,
            "status": "failed",
            "failure_reason": "缺少已完成的通用无 ROI 预算参考，不能评估 ROI 是否预算中性",
            "output_path": None,
            "width": None,
            "height": None,
            "resolution": None,
            "average_video_packet_bitrate_bps": None,
            "average_video_packet_bitrate_mbps": None,
            "saving_vs_default_pct": None,
            "saving_vs_general_no_roi_pct": None,
            "target_saving_min_pct": target_min_pct,
            "target_saving_max_pct": target_max_pct,
            "target_saving_met": False,
            "saving_target_status": "not_generated",
            "selection_reason": "missing_budget_reference",
            "cache_hit": False,
            "thresholds": thresholds.to_dict(),
            "short_search": None,
            "target_selection": None,
            "full_attempts": [],
            "config": scheme.to_dict(full_reference.video.fps),
            "conditions": conditions.to_dict(),
            "adaptive_quantization": aq.to_dict(),
            "roi": roi.to_dict() if roi else None,
            "denoise": denoise.to_dict() if denoise else None,
        }
    short_root = output_dir / "work" / "short" / strategy.public_mode
    full_root = output_dir / "work" / "full" / strategy.public_mode
    _notify_progress(progress_callback, f"searching_{strategy.public_mode}")
    print(f"[{strategy.public_mode}] 正在用前12秒独立搜索本策略 CRF……", flush=True)
    search = run_scheme_quality_search(
        toolchain=toolchain,
        input_source=input_source,
        reference=short_reference,
        output_dir=short_root,
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        min_speed_x=None,
        adaptive_quantization=aq,
        roi_settings=roi,
        denoise_settings=denoise,
        crf_min=strategy.crf_search_min,
        crf_max=strategy.crf_search_max,
        crf_step=strategy.crf_search_step,
    )
    short_points = _expand_target_search_points(
        toolchain=toolchain,
        input_source=input_source,
        reference=short_reference,
        output_dir=short_root,
        scheme=scheme,
        conditions=conditions,
        thresholds=thresholds,
        default_bitrate_bps=default_bitrate_bps,
        target_min_pct=target_min_pct,
        target_max_pct=target_max_pct,
        initial_points=search.points,
        adaptive_quantization=aq,
        roi_settings=roi,
        denoise_settings=denoise,
        crf_min=strategy.crf_search_min,
        crf_max=strategy.crf_search_max,
        crf_step=strategy.crf_search_step,
    )
    selected_short, target_selection = _select_short_candidate(
        short_points,
        default_bitrate_bps,
        target_min_pct,
        target_max_pct,
    )
    _notify_progress(progress_callback, f"validating_{strategy.public_mode}")
    destination = (output_dir / strategy.output_filename).resolve()
    if selected_short is None:
        destination.unlink(missing_ok=True)
        return {
            **strategy_metadata,
            "strategy_id": strategy.strategy_id,
            "title": strategy.title,
            "mode": strategy.public_mode,
            "status": "failed",
            "failure_reason": (
                f"前12秒在 CRF {strategy.crf_search_min:.1f}～"
                f"{strategy.crf_search_max:.1f} 内没有满足绝对画质门槛的点"
            ),
            "output_path": None,
            "width": None,
            "height": None,
            "resolution": None,
            "average_video_packet_bitrate_bps": None,
            "average_video_packet_bitrate_mbps": None,
            "saving_vs_default_pct": None,
            "saving_vs_general_no_roi_pct": None,
            "target_saving_min_pct": target_min_pct,
            "target_saving_max_pct": target_max_pct,
            "target_saving_met": target_selection["target_saving_met"],
            "saving_target_status": target_selection["saving_target_status"],
            "selection_reason": target_selection["selection_reason"],
            "cache_hit": False,
            "thresholds": thresholds.to_dict(),
            "short_search": search.to_dict(),
            "target_selection": target_selection,
            "full_attempts": [],
            "config": scheme.to_dict(short_reference.video.fps),
            "conditions": conditions.to_dict(),
            "adaptive_quantization": aq.to_dict(),
            "roi": roi.to_dict() if roi else None,
            "denoise": denoise.to_dict() if denoise else None,
        }

    full_attempts: List[CandidateResult] = []
    crf = selected_short.crf
    selected_full: Optional[CandidateResult] = None
    while crf >= strategy.crf_search_min - 1e-9:
        print(f"[{strategy.public_mode}] 完整视频验证 CRF {crf:.1f}……", flush=True)
        candidate = evaluate_scheme_crf(
            toolchain=toolchain,
            input_source=input_source,
            reference=full_reference,
            output_dir=full_root,
            scheme=scheme,
            conditions=conditions,
            thresholds=thresholds,
            min_speed_x=None,
            crf=crf,
            adaptive_quantization=aq,
            roi_settings=roi,
            denoise_settings=denoise,
        )
        full_attempts.append(candidate)
        if candidate.quality_pass:
            selected_full = candidate
            break
        crf = round(crf - strategy.crf_search_step, 8)

    if selected_full is None:
        destination.unlink(missing_ok=True)
        return {
            **strategy_metadata,
            "strategy_id": strategy.strategy_id,
            "title": strategy.title,
            "mode": strategy.public_mode,
            "status": "failed",
            "failure_reason": (
                f"完整视频降低到 CRF {strategy.crf_search_min:.1f} "
                "后仍未达到绝对画质门槛"
            ),
            "output_path": None,
            "width": None,
            "height": None,
            "resolution": None,
            "average_video_packet_bitrate_bps": None,
            "average_video_packet_bitrate_mbps": None,
            "saving_vs_default_pct": None,
            "saving_vs_general_no_roi_pct": None,
            "target_saving_min_pct": target_min_pct,
            "target_saving_max_pct": target_max_pct,
            "target_saving_met": False if target_min_pct is not None else None,
            "saving_target_status": "not_generated",
            "selection_reason": "full_validation_failed_after_crf_fallback",
            "cache_hit": False,
            "thresholds": thresholds.to_dict(),
            "short_search": search.to_dict(),
            "target_selection": target_selection,
            "full_attempts": [item.to_dict() for item in full_attempts],
            "config": scheme.to_dict(full_reference.video.fps),
            "conditions": conditions.to_dict(),
            "adaptive_quantization": aq.to_dict(),
            "roi": roi.to_dict() if roi else None,
            "denoise": denoise.to_dict() if denoise else None,
        }

    attempted_saving_vs_general = None
    if budget_bitrate_bps is not None:
        attempted_saving_vs_general = bitrate_saving_vs_default_pct(
            budget_bitrate_bps,
            selected_full.bitrate_bps,
        )
        strategy_metadata["saving_vs_general_no_roi_pct"] = attempted_saving_vs_general
        strategy_metadata["budget_margin_bps"] = (
            budget_bitrate_bps - selected_full.bitrate_bps
        )
    if strategy.budget_neutral_required:
        budget_pass = (
            budget_bitrate_bps is not None
            and selected_full.bitrate_bps <= budget_bitrate_bps + 1e-6
        )
        strategy_metadata["budget_neutral_pass"] = budget_pass
        if not budget_pass:
            destination.unlink(missing_ok=True)
            return {
                **strategy_metadata,
                "strategy_id": strategy.strategy_id,
                "title": strategy.title,
                "mode": strategy.public_mode,
                "status": "failed",
                "failure_reason": (
                    "ROI 候选平均视频包码率 "
                    f"{_format_budget_mbps(selected_full.bitrate_bps)} 超过通用无 ROI 预算 "
                    f"{_format_budget_mbps(budget_bitrate_bps)}"
                ),
                "output_path": None,
                "width": None,
                "height": None,
                "resolution": None,
                "average_video_packet_bitrate_bps": None,
                "average_video_packet_bitrate_mbps": None,
                "attempted_average_video_packet_bitrate_bps": selected_full.bitrate_bps,
                "attempted_average_video_packet_bitrate_mbps": (
                    selected_full.bitrate_bps / 1_000_000.0
                ),
                "saving_vs_default_pct": None,
                "selected_crf": selected_full.crf,
                "vmaf_mean": selected_full.vmaf_mean,
                "vmaf_p5": selected_full.vmaf_p5,
                "ssim": selected_full.ssim,
                "encode_speed_x": selected_full.encode_speed_x,
                "target_saving_min_pct": target_min_pct,
                "target_saving_max_pct": target_max_pct,
                "target_saving_met": False,
                "saving_target_status": "not_generated",
                "selection_reason": "roi_budget_exceeded",
                "cache_hit": False,
                "thresholds": thresholds.to_dict(),
                "short_search": search.to_dict(),
                "target_selection": target_selection,
                "full_attempts": [item.to_dict() for item in full_attempts],
                "selected_candidate": selected_full.to_dict(),
                "config": scheme.to_dict(full_reference.video.fps),
                "conditions": conditions.to_dict(),
                "adaptive_quantization": aq.to_dict(),
                "roi": roi.to_dict() if roi else None,
                "denoise": denoise.to_dict() if denoise else None,
            }

    if strategy.roi_quality_required:
        region_quality = evaluate_important_regions(
            toolchain,
            budget_candidate,
            selected_full,
            full_reference.video.path,
            full_reference.cache_key,
            important_regions(roi),
            output_dir,
        )
        roi_quality_summary = _summarize_roi_region_quality(region_quality)
        strategy_metadata.update(roi_quality_summary)
        if not roi_quality_summary["roi_quality_preserved"]:
            destination.unlink(missing_ok=True)
            return {
                **strategy_metadata,
                "strategy_id": strategy.strategy_id,
                "title": strategy.title,
                "mode": strategy.public_mode,
                "status": "failed",
                "failure_reason": "；".join(
                    roi_quality_summary["roi_quality_failure_reasons"]
                ),
                "output_path": None,
                "width": None,
                "height": None,
                "resolution": None,
                "average_video_packet_bitrate_bps": None,
                "average_video_packet_bitrate_mbps": None,
                "attempted_average_video_packet_bitrate_bps": selected_full.bitrate_bps,
                "attempted_average_video_packet_bitrate_mbps": (
                    selected_full.bitrate_bps / 1_000_000.0
                ),
                "saving_vs_default_pct": None,
                "selected_crf": selected_full.crf,
                "vmaf_mean": selected_full.vmaf_mean,
                "vmaf_p5": selected_full.vmaf_p5,
                "ssim": selected_full.ssim,
                "encode_speed_x": selected_full.encode_speed_x,
                "target_saving_min_pct": target_min_pct,
                "target_saving_max_pct": target_max_pct,
                "target_saving_met": False,
                "saving_target_status": "not_generated",
                "selection_reason": "roi_region_quality_decreased",
                "cache_hit": False,
                "thresholds": thresholds.to_dict(),
                "short_search": search.to_dict(),
                "target_selection": target_selection,
                "full_attempts": [item.to_dict() for item in full_attempts],
                "selected_candidate": selected_full.to_dict(),
                "config": scheme.to_dict(full_reference.video.fps),
                "conditions": conditions.to_dict(),
                "adaptive_quantization": aq.to_dict(),
                "roi": roi.to_dict() if roi else None,
                "denoise": denoise.to_dict() if denoise else None,
            }

    publish_cache_hit = _publish_candidate(selected_full, destination)
    output = probe_video(toolchain.ffprobe, destination)
    saving_vs_default = bitrate_saving_vs_default_pct(
        default_bitrate_bps,
        output.video_bitrate_bps,
    )
    saving_vs_general = (
        None
        if budget_bitrate_bps is None
        else bitrate_saving_vs_default_pct(
            budget_bitrate_bps,
            output.video_bitrate_bps,
        )
    )
    if strategy.strategy_id == "generic_no_roi":
        saving_vs_general = 0.0
    budget_neutral_pass = (
        strategy_metadata["budget_neutral_pass"]
        if strategy.budget_neutral_required
        else None
    )
    target_saving_met, saving_target_status = _saving_target_status(
        saving_vs_default,
        target_min_pct,
        target_max_pct,
    )
    return {
        **strategy_metadata,
        "strategy_id": strategy.strategy_id,
        "title": strategy.title,
        "mode": strategy.public_mode,
        "status": "completed",
        "failure_reason": None,
        "output_path": str(destination),
        "width": output.width,
        "height": output.height,
        "resolution": f"{output.width}x{output.height}",
        "average_video_packet_bitrate_bps": output.video_bitrate_bps,
        "average_video_packet_bitrate_mbps": output.video_bitrate_bps / 1_000_000.0,
        "saving_vs_default_pct": saving_vs_default,
        "saving_vs_general_no_roi_pct": saving_vs_general,
        "budget_neutral_pass": budget_neutral_pass,
        "budget_margin_bps": (
            None if budget_bitrate_bps is None else budget_bitrate_bps - output.video_bitrate_bps
        ),
        "target_saving_min_pct": target_min_pct,
        "target_saving_max_pct": target_max_pct,
        "target_saving_met": target_saving_met,
        "saving_target_status": saving_target_status,
        "selection_reason": target_selection["selection_reason"],
        "cache_hit": bool(
            publish_cache_hit
            and short_points
            and all(item.cache_hit for item in short_points)
            and all(item.cache_hit for item in full_attempts)
        ),
        "selected_crf": selected_full.crf,
        "vmaf_mean": selected_full.vmaf_mean,
        "vmaf_p5": selected_full.vmaf_p5,
        "ssim": selected_full.ssim,
        "encode_speed_x": selected_full.encode_speed_x,
        "thresholds": thresholds.to_dict(),
        "short_search": search.to_dict(),
        "target_selection": target_selection,
        "full_attempts": [item.to_dict() for item in full_attempts],
        "selected_candidate": selected_full.to_dict(),
        "config": scheme.to_dict(full_reference.video.fps),
        "conditions": conditions.to_dict(),
        "adaptive_quantization": aq.to_dict(),
        "roi": roi.to_dict() if roi else None,
        "denoise": denoise.to_dict() if denoise else None,
    }


def run_multi_encode(
    toolchain: Toolchain,
    input_path: Path,
    roi_config_path: Path,
    output_dir: Path,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    input_source = probe_video(toolchain.ffprobe, input_path)
    _notify_progress(progress_callback, "preparing_reference")
    full_reference = prepare_reference(
        toolchain=toolchain,
        input_path=input_path,
        output_dir=output_dir / "work" / "full_reference",
        start_seconds=0.0,
        duration_seconds=input_source.duration_seconds,
        source=input_source,
    )
    _notify_progress(progress_callback, "encoding_h264_native")
    strategies: List[Dict] = [
        _h264_native_strategy(toolchain, full_reference, output_dir)
    ]
    default_bitrate = strategies[0]["average_video_packet_bitrate_bps"]
    strategies.append(
        _fixed_hevc_strategy(
            toolchain=toolchain,
            input_source=input_source,
            full_reference=full_reference,
            output_dir=output_dir,
            default_bitrate_bps=default_bitrate,
            progress_callback=progress_callback,
        )
    )

    payload = {
        "schema_version": MULTI_ENCODE_SCHEMA_VERSION,
        "pipeline_version": MULTI_ENCODE_PIPELINE_VERSION,
    "study": "V2.0 H.264 原生编码与 H.265 固定参数方案",
        "input": input_source.to_dict(),
        "short_reference": None,
        "full_reference": full_reference.to_dict(),
        "comparison_policy": {
            "crf_pairing": False,
            "quality_delta_matching": False,
            "winner_selection": False,
            "deployment_conclusion": False,
            "saving_is_informational_only": True,
            "default_strategy_id": "default_h264",
            "fixed_hevc_crf": V1_6_HEVC_FIXED_CRF,
            "roi_enabled": False,
            "denoise_enabled": False,
        },
        "strategies": strategies,
    }
    return write_multi_encode_reports(output_dir, payload)
