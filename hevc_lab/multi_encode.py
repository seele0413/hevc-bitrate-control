import hashlib
import json
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .adapters import prepare_reference, probe_video
from .adapters.reference import sha256_file
from .core.configs import (
    available_modes,
    default_aq_profile,
    denoise_policy_for_mode,
    get_mode_policy,
    v1_comparison_plan,
)
from .core.models import CandidateResult, DenoiseSettings, ReferenceArtifact, Toolchain
from .core.roi import load_roi_settings
from .core.search import QualityThresholds
from .encoders import encode_default_x265
from .quality_search import evaluate_scheme_crf, run_scheme_quality_search
from .reports import write_multi_encode_reports


MULTI_ENCODE_SCHEMA_VERSION = 1
MULTI_ENCODE_PIPELINE_VERSION = "v0.11.0"
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
        "status": "completed",
        "output_path": str(destination),
        "width": output.width,
        "height": output.height,
        "resolution": f"{output.width}x{output.height}",
        "average_video_packet_bitrate_bps": output.video_bitrate_bps,
        "average_video_packet_bitrate_mbps": output.video_bitrate_bps / 1_000_000.0,
        "saving_vs_default_pct": None,
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


def _composite_strategy(
    toolchain: Toolchain,
    input_source,
    short_reference: ReferenceArtifact,
    full_reference: ReferenceArtifact,
    roi_config_path: Path,
    output_dir: Path,
    mode: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict:
    plan = v1_comparison_plan(mode)
    policy = get_mode_policy(mode)
    scheme = plan.optimized
    conditions = plan.conditions
    thresholds = QualityThresholds(
        vmaf_mean=policy.target_vmaf,
        vmaf_p5=policy.target_vmaf_p5,
        ssim=policy.target_ssim,
    )
    aq = default_aq_profile()
    roi = load_roi_settings(roi_config_path, mode)
    roi.validate_input(input_source.width, input_source.height)
    denoise = DenoiseSettings(roi=roi, policy=denoise_policy_for_mode(mode))
    short_root = output_dir / "work" / "short" / mode
    full_root = output_dir / "work" / "full" / mode
    _notify_progress(progress_callback, f"searching_{mode}")
    print(f"[{mode}] 正在用前12秒独立搜索综合策略 CRF……", flush=True)
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
    )
    _notify_progress(progress_callback, f"validating_{mode}")
    destination = (output_dir / f"composite_{mode}.mp4").resolve()
    if search.selected is None:
        destination.unlink(missing_ok=True)
        return {
            "strategy_id": f"composite_{mode}",
            "title": f"{policy.title}综合策略",
            "mode": mode,
            "status": "failed",
            "failure_reason": "前12秒在 CRF 18～38 内没有满足绝对画质门槛的点",
            "output_path": None,
            "width": None,
            "height": None,
            "resolution": None,
            "average_video_packet_bitrate_bps": None,
            "average_video_packet_bitrate_mbps": None,
            "saving_vs_default_pct": None,
            "cache_hit": False,
            "thresholds": thresholds.to_dict(),
            "short_search": search.to_dict(),
            "full_attempts": [],
            "config": scheme.to_dict(short_reference.video.fps),
            "conditions": conditions.to_dict(),
            "adaptive_quantization": aq.to_dict(),
            "roi": roi.to_dict(),
            "denoise": denoise.to_dict(),
        }

    full_attempts: List[CandidateResult] = []
    crf = search.selected.crf
    selected_full: Optional[CandidateResult] = None
    while crf >= CRF_MIN - 1e-9:
        print(f"[{mode}] 完整视频验证 CRF {crf:.1f}……", flush=True)
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
        crf = round(crf - CRF_STEP, 8)

    if selected_full is None:
        destination.unlink(missing_ok=True)
        return {
            "strategy_id": f"composite_{mode}",
            "title": f"{policy.title}综合策略",
            "mode": mode,
            "status": "failed",
            "failure_reason": "完整视频降低到 CRF 18 后仍未达到绝对画质门槛",
            "output_path": None,
            "width": None,
            "height": None,
            "resolution": None,
            "average_video_packet_bitrate_bps": None,
            "average_video_packet_bitrate_mbps": None,
            "saving_vs_default_pct": None,
            "cache_hit": False,
            "thresholds": thresholds.to_dict(),
            "short_search": search.to_dict(),
            "full_attempts": [item.to_dict() for item in full_attempts],
            "config": scheme.to_dict(full_reference.video.fps),
            "conditions": conditions.to_dict(),
            "adaptive_quantization": aq.to_dict(),
            "roi": roi.to_dict(),
            "denoise": denoise.to_dict(),
        }

    publish_cache_hit = _publish_candidate(selected_full, destination)
    output = probe_video(toolchain.ffprobe, destination)
    return {
        "strategy_id": f"composite_{mode}",
        "title": f"{policy.title}综合策略",
        "mode": mode,
        "status": "completed",
        "failure_reason": None,
        "output_path": str(destination),
        "width": output.width,
        "height": output.height,
        "resolution": f"{output.width}x{output.height}",
        "average_video_packet_bitrate_bps": output.video_bitrate_bps,
        "average_video_packet_bitrate_mbps": output.video_bitrate_bps / 1_000_000.0,
        "saving_vs_default_pct": None,
        "cache_hit": bool(
            publish_cache_hit
            and search.points
            and all(item.cache_hit for item in search.points)
            and all(item.cache_hit for item in full_attempts)
        ),
        "selected_crf": selected_full.crf,
        "vmaf_mean": selected_full.vmaf_mean,
        "vmaf_p5": selected_full.vmaf_p5,
        "ssim": selected_full.ssim,
        "encode_speed_x": selected_full.encode_speed_x,
        "thresholds": thresholds.to_dict(),
        "short_search": search.to_dict(),
        "full_attempts": [item.to_dict() for item in full_attempts],
        "config": scheme.to_dict(full_reference.video.fps),
        "conditions": conditions.to_dict(),
        "adaptive_quantization": aq.to_dict(),
        "roi": roi.to_dict(),
        "denoise": denoise.to_dict(),
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
    load_roi_settings(roi_config_path, "balanced").validate_input(
        input_source.width,
        input_source.height,
    )
    _notify_progress(progress_callback, "preparing_reference")
    short_duration = min(SHORT_SEARCH_SECONDS, input_source.duration_seconds)
    short_reference = prepare_reference(
        toolchain=toolchain,
        input_path=input_path,
        output_dir=output_dir / "work" / "short_reference",
        start_seconds=0.0,
        duration_seconds=short_duration,
        source=input_source,
    )
    full_reference = prepare_reference(
        toolchain=toolchain,
        input_path=input_path,
        output_dir=output_dir / "work" / "full_reference",
        start_seconds=0.0,
        duration_seconds=input_source.duration_seconds,
        source=input_source,
    )
    _notify_progress(progress_callback, "encoding_default")
    strategies: List[Dict] = [
        _default_strategy(toolchain, full_reference, output_dir)
    ]
    for mode in available_modes():
        strategies.append(
            _composite_strategy(
                toolchain=toolchain,
                input_source=input_source,
                short_reference=short_reference,
                full_reference=full_reference,
                roi_config_path=roi_config_path,
                output_dir=output_dir,
                mode=mode,
                progress_callback=progress_callback,
            )
        )

    default_bitrate = strategies[0]["average_video_packet_bitrate_bps"]
    for strategy in strategies[1:]:
        bitrate = strategy.get("average_video_packet_bitrate_bps")
        if strategy["status"] == "completed" and bitrate is not None:
            strategy["saving_vs_default_pct"] = bitrate_saving_vs_default_pct(
                default_bitrate,
                bitrate,
            )

    payload = {
        "schema_version": MULTI_ENCODE_SCHEMA_VERSION,
        "pipeline_version": MULTI_ENCODE_PIPELINE_VERSION,
        "study": "x265默认编码与三档综合策略独立输出",
        "input": input_source.to_dict(),
        "short_reference": short_reference.to_dict(),
        "full_reference": full_reference.to_dict(),
        "comparison_policy": {
            "crf_pairing": False,
            "quality_delta_matching": False,
            "winner_selection": False,
            "deployment_conclusion": False,
            "saving_is_informational_only": True,
        },
        "strategies": strategies,
    }
    return write_multi_encode_reports(output_dir, payload)
