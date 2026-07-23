import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import __version__
from .adapters.reference import sha256_file
from .core.configs import get_mode_policy, v1_comparison_plan
from .core.models import Toolchain
from .core.speed import validate_speed_gate
from .errors import VideoError
from .pair_search import run_pair_quality_search


COMPARISON_CACHE_SCHEMA_VERSION = 1
COMPARISON_PIPELINE_VERSION = "compare-pair-continuity-v2"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def build_comparison_request(
    toolchain: Toolchain,
    input_path: Path,
    mode: str,
    preset: Optional[str],
    target_vmaf: Optional[float],
    target_vmaf_p5: Optional[float],
    target_ssim: Optional[float],
    min_speed: Optional[float],
    min_saving: Optional[float],
    min_algorithm_saving: Optional[float],
    min_source_saving: Optional[float],
    max_vmaf_delta: float,
    start_seconds: float,
    duration_seconds: float,
) -> dict:
    input_path = input_path.expanduser().resolve()
    if not input_path.is_file():
        raise VideoError(f"输入视频不存在：{input_path}")
    policy = get_mode_policy(mode)
    plan = v1_comparison_plan(mode)
    algorithm_saving = (
        min_algorithm_saving
        if min_algorithm_saving is not None
        else min_saving
        if min_saving is not None
        else policy.min_algorithm_saving_pct
    )
    source_saving = (
        min_source_saving
        if min_source_saving is not None
        else min_saving
        if min_saving is not None
        else policy.min_source_saving_pct
    )
    effective_speed = policy.min_speed_x if min_speed is None else min_speed
    if algorithm_saving < 0 or source_saving < 0:
        raise ValueError("节省门槛不能小于0")
    validate_speed_gate(effective_speed)
    if max_vmaf_delta < 0:
        raise ValueError("最大VMAF差不能小于0")
    return {
        "pipeline_version": COMPARISON_PIPELINE_VERSION,
        "hevc_lab_version": __version__,
        "input_path": str(input_path),
        "input_sha256": sha256_file(input_path),
        "vmaf_model_sha256": sha256_file(toolchain.vmaf_model),
        "mode": mode,
        "preset": preset or plan.conditions.preset,
        "preset_override": preset,
        "mode_default_preset": plan.conditions.preset,
        "preset_source": "override" if preset is not None else "mode",
        "target_vmaf": policy.target_vmaf if target_vmaf is None else target_vmaf,
        "target_vmaf_p5": (
            policy.target_vmaf_p5
            if target_vmaf_p5 is None
            else target_vmaf_p5
        ),
        "target_ssim": policy.target_ssim if target_ssim is None else target_ssim,
        "min_speed_x": effective_speed,
        "min_algorithm_saving_pct": algorithm_saving,
        "min_source_saving_pct": source_saving,
        "max_vmaf_delta": max_vmaf_delta,
        "start_seconds": start_seconds,
        "duration_seconds": duration_seconds,
    }


def comparison_cache_key(request: dict) -> str:
    serialized = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _completed_result_is_valid(payload: dict, cache_key: str) -> bool:
    cache = payload.get("comparison_cache") or {}
    if cache.get("cache_key") != cache_key:
        return False
    pair = (payload.get("match") or {}).get("pair")
    if pair:
        for side in ("baseline", "optimized"):
            output_path = (pair.get(side) or {}).get("output_path")
            if not output_path or not Path(output_path).is_file():
                return False
    continuity = payload.get("continuity_validation") or {}
    if continuity.get("checked"):
        output_path = continuity.get("output_path")
        if not output_path or not Path(output_path).is_file():
            return False
    return True


def run_comparison(
    toolchain: Toolchain,
    input_path: Path,
    output_dir: Path,
    mode: str = "balanced",
    preset: Optional[str] = None,
    target_vmaf: Optional[float] = None,
    target_vmaf_p5: Optional[float] = None,
    target_ssim: Optional[float] = None,
    min_speed: Optional[float] = None,
    min_saving: Optional[float] = None,
    min_algorithm_saving: Optional[float] = None,
    min_source_saving: Optional[float] = None,
    max_vmaf_delta: float = 1.0,
    start_seconds: float = 0.0,
    duration_seconds: float = 15.0,
) -> dict:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "comparison_state.json"
    result_path = output_dir / "comparison.json"
    request = build_comparison_request(
        toolchain,
        input_path,
        mode,
        preset,
        target_vmaf,
        target_vmaf_p5,
        target_ssim,
        min_speed,
        min_saving,
        min_algorithm_saving,
        min_source_saving,
        max_vmaf_delta,
        start_seconds,
        duration_seconds,
    )
    cache_key = comparison_cache_key(request)
    previous_state = _load_json(state_path)
    previous_result = _load_json(result_path)
    same_request = bool(
        previous_state and previous_state.get("cache_key") == cache_key
    )
    if (
        same_request
        and previous_state.get("status") == "completed"
        and previous_result
        and _completed_result_is_valid(previous_result, cache_key)
    ):
        cached = dict(previous_result)
        cached["comparison_cache"] = {
            **cached.get("comparison_cache", {}),
            "experiment_cache_hit": True,
        }
        return cached

    resumed = bool(same_request and previous_state.get("status") != "completed")
    attempt = int(previous_state.get("attempt", 0)) + 1 if same_request else 1
    state = {
        "schema_version": COMPARISON_CACHE_SCHEMA_VERSION,
        "pipeline_version": COMPARISON_PIPELINE_VERSION,
        "cache_key": cache_key,
        "status": "running",
        "stage": "initializing",
        "attempt": attempt,
        "resumed": resumed,
        "request": request,
        "started_at": (
            previous_state.get("started_at") if same_request else _now()
        ),
        "attempt_started_at": _now(),
        "updated_at": _now(),
        "completed_at": None,
        "last_error": None,
    }
    _atomic_json_write(state_path, state)

    def progress(stage: str) -> None:
        state["stage"] = stage
        state["updated_at"] = _now()
        _atomic_json_write(state_path, state)

    try:
        pair_payload = run_pair_quality_search(
            toolchain=toolchain,
            input_path=Path(request["input_path"]),
            output_dir=output_dir,
            mode=mode,
            preset=request["preset_override"],
            target_vmaf=request["target_vmaf"],
            target_vmaf_p5=request["target_vmaf_p5"],
            target_ssim=request["target_ssim"],
            min_speed=request["min_speed_x"],
            min_algorithm_saving=request["min_algorithm_saving_pct"],
            min_source_saving=request["min_source_saving_pct"],
            max_vmaf_delta=request["max_vmaf_delta"],
            start_seconds=request["start_seconds"],
            duration_seconds=request["duration_seconds"],
            progress_callback=progress,
        )
        baseline_points = pair_payload["searches"]["baseline"]["points"]
        optimized_points = pair_payload["searches"]["optimized"]["points"]
        payload = {
            **pair_payload,
            "command": "compare",
            "comparison_request": request,
            "comparison_cache": {
                "schema_version": COMPARISON_CACHE_SCHEMA_VERSION,
                "pipeline_version": COMPARISON_PIPELINE_VERSION,
                "cache_key": cache_key,
                "experiment_cache_hit": False,
                "resumed": resumed,
                "attempt": attempt,
                "state_path": str(state_path),
                "candidate_cache_hits": {
                    "baseline": sum(bool(point["cache_hit"]) for point in baseline_points),
                    "baseline_total": len(baseline_points),
                    "optimized": sum(bool(point["cache_hit"]) for point in optimized_points),
                    "optimized_total": len(optimized_points),
                },
            },
        }
        progress("write_comparison_result")
        _atomic_json_write(result_path, payload)
        state.update(
            {
                "status": "completed",
                "stage": "completed",
                "updated_at": _now(),
                "completed_at": _now(),
            }
        )
        _atomic_json_write(state_path, state)
        return payload
    except Exception as exc:
        state.update(
            {
                "status": "failed",
                "updated_at": _now(),
                "last_error": f"{type(exc).__name__}: {exc}",
            }
        )
        _atomic_json_write(state_path, state)
        raise
