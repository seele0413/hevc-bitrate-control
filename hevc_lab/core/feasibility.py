from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .matching import EqualQualityMatchResult
from .models import ModePolicy, ReferenceArtifact, VideoInfo
from .selection import calculate_saving
from .speed import (
    ENGINEERING_HEADROOM_THRESHOLD_X,
    REALTIME_SPEED_THRESHOLD_X,
    classify_speed,
    speed_gate_passes,
)


@dataclass(frozen=True)
class ContinuityValidation:
    checked: bool
    passed: bool
    output_path: str
    reason: str
    checks: Dict[str, bool]
    metrics: Dict[str, Any]

    @classmethod
    def not_checked(cls, reason: str) -> "ContinuityValidation":
        return cls(False, False, "", reason, {}, {})

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "passed": self.passed,
            "output_path": self.output_path,
            "reason": self.reason,
            "checks": dict(self.checks),
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class FeasibilityConclusion:
    name: str
    title: str
    passed: bool
    decision: str
    reason: str
    checks: Dict[str, bool]
    metrics: Dict[str, Any]
    thresholds: Dict[str, Optional[float]]
    scope: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "passed": self.passed,
            "decision": self.decision,
            "reason": self.reason,
            "checks": dict(self.checks),
            "metrics": dict(self.metrics),
            "thresholds": dict(self.thresholds),
            "scope": self.scope,
        }


@dataclass(frozen=True)
class FeasibilityConclusions:
    algorithm: FeasibilityConclusion
    software_continuity: FeasibilityConclusion
    deployment: FeasibilityConclusion

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm.to_dict(),
            "software_continuity": self.software_continuity.to_dict(),
            "deployment": self.deployment.to_dict(),
        }


def assess_video_continuity(
    reference: ReferenceArtifact,
    output: VideoInfo,
    timestamps: Iterable[float],
    decoded_without_error: bool,
    decode_error: str = "",
) -> ContinuityValidation:
    values = [float(value) for value in timestamps]
    deltas = [current - previous for previous, current in zip(values, values[1:])]
    frame_interval = 1.0 / reference.video.fps
    fps_tolerance = max(0.001, reference.video.fps * 0.0001)
    time_tolerance = max(0.001, frame_interval)
    first_timestamp = values[0] if values else None
    min_delta = min(deltas) if deltas else 0.0
    max_delta = max(deltas) if deltas else 0.0
    strictly_increasing = bool(values) and all(delta > 0.0 for delta in deltas)
    checks = {
        "decoded_without_error": decoded_without_error,
        "resolution_match": (output.width, output.height)
        == (reference.video.width, reference.video.height),
        "fps_match": abs(output.fps - reference.video.fps) <= fps_tolerance,
        "frame_count_match": len(values) == reference.frame_count,
        "duration_match": abs(
            output.duration_seconds - reference.effective_duration_seconds
        )
        <= time_tolerance,
        "timestamp_zero_based": bool(values)
        and abs(values[0]) <= max(0.001, 0.5 * frame_interval),
        "timestamps_strictly_increasing": strictly_increasing,
        "max_frame_gap": bool(values)
        and (not deltas or max_delta <= 1.5 * frame_interval + 1e-9),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if not failed:
        reason = "全文件解码、规格、帧数、时长和逐帧时间戳连续性全部通过。"
    else:
        reason = "连续性检查未通过：" + "、".join(failed) + "。"
        if decode_error:
            reason += f" 解码错误：{decode_error[-500:]}"
    return ContinuityValidation(
        checked=True,
        passed=all(checks.values()),
        output_path=str(Path(output.path).resolve()),
        reason=reason,
        checks=checks,
        metrics={
            "reference_fps": reference.video.fps,
            "output_fps": output.fps,
            "reference_frame_count": float(reference.frame_count),
            "output_frame_count": float(len(values)),
            "reference_duration_seconds": reference.effective_duration_seconds,
            "output_duration_seconds": output.duration_seconds,
            "first_timestamp_seconds": first_timestamp,
            "min_frame_delta_seconds": min_delta,
            "max_frame_delta_seconds": max_delta,
            "max_allowed_frame_gap_seconds": 1.5 * frame_interval,
        },
    )


def evaluate_feasibility(
    match: EqualQualityMatchResult,
    mode: ModePolicy,
    source_video_bitrate_bps: float,
    continuity: ContinuityValidation,
) -> FeasibilityConclusions:
    """按 DESIGN_V1 第9节生成三种彼此分离的正式结论。"""
    pair = match.pair
    pair_found = pair is not None
    algorithm_saving = pair.algorithm_saving_pct if pair else None
    strict_positive = bool(pair and pair.algorithm_saving_pct > 0.0)
    algorithm_threshold_pass = bool(
        pair and pair.algorithm_saving_pct >= mode.min_algorithm_saving_pct
    )
    algorithm_passed = pair_found and strict_positive and algorithm_threshold_pass

    if not pair:
        algorithm_decision = "insufficient_evidence"
        algorithm_reason = f"未找到等画质候选对：{match.reason}。"
    elif algorithm_passed:
        algorithm_decision = "effective"
        algorithm_reason = (
            f"等画质优化组合节省 {pair.algorithm_saving_pct:.2f}%，"
            f"达到{mode.title} {mode.min_algorithm_saving_pct:.2f}% 的算法门槛。"
        )
    elif not strict_positive:
        algorithm_decision = "not_effective"
        algorithm_reason = (
            f"等画质优化组合码率变化为 {pair.algorithm_saving_pct:.2f}%，"
            "未取得严格正收益。"
        )
    else:
        algorithm_decision = "not_effective"
        algorithm_reason = (
            f"等画质优化组合节省 {pair.algorithm_saving_pct:.2f}%，"
            f"未达到{mode.title} {mode.min_algorithm_saving_pct:.2f}% 的算法门槛。"
        )
    algorithm = FeasibilityConclusion(
        name="algorithm",
        title="算法可行性",
        passed=algorithm_passed,
        decision=algorithm_decision,
        reason=algorithm_reason,
        checks={
            "equal_quality_pair_found": pair_found,
            "strictly_positive_saving": strict_positive,
            "mode_algorithm_saving_threshold": algorithm_threshold_pass,
        },
        metrics={
            "algorithm_saving_pct": algorithm_saving,
            "vmaf_delta": pair.vmaf_delta if pair else None,
        },
        thresholds={
            "min_algorithm_saving_pct": mode.min_algorithm_saving_pct,
            "max_vmaf_delta": match.max_vmaf_delta,
        },
        scope="只证明本样本、当前 libx265 和实验参数下的帧间优化组合是否有效。",
    )

    optimized_speed = pair.optimized.encode_speed_x if pair else None
    speed_gate_enabled = mode.min_speed_x is not None
    continuous_speed = bool(
        pair and speed_gate_passes(pair.optimized.encode_speed_x, mode.min_speed_x)
    )
    realtime_capable = bool(
        pair and pair.optimized.encode_speed_x >= REALTIME_SPEED_THRESHOLD_X
    )
    engineering_headroom = bool(
        pair
        and pair.optimized.encode_speed_x >= ENGINEERING_HEADROOM_THRESHOLD_X
    )
    speed_tier = (
        classify_speed(pair.optimized.encode_speed_x, mode.min_speed_x)
        if pair
        else None
    )
    continuity_checks = dict(continuity.checks)
    continuity_checks["continuity_was_checked"] = continuity.checked
    continuity_checks["speed_gate_enabled"] = speed_gate_enabled
    continuity_checks["encode_speed_gate_passed"] = continuous_speed
    continuity_checks["encode_speed_at_least_1x_information_only"] = realtime_capable
    continuity_checks[
        "encode_speed_at_least_1_1x_information_only"
    ] = engineering_headroom
    continuity_passed = (
        continuity.checked
        and continuity.passed
        and (not speed_gate_enabled or continuous_speed)
    )
    if not continuity.checked:
        continuity_decision = "not_checked"
        continuity_reason = continuity.reason
    elif not continuity.passed:
        continuity_decision = "not_continuous"
        continuity_reason = continuity.reason
    elif speed_gate_enabled and not continuous_speed:
        continuity_decision = "offline_only"
        continuity_reason = (
            f"{continuity.reason} 但编码速度 {optimized_speed:.3f}x 低于 "
            f"{mode.min_speed_x:.3f}x，"
            "只能离线编码。"
        )
    elif engineering_headroom:
        continuity_decision = "realtime_continuous_headroom"
        continuity_reason = (
            f"{continuity.reason} 编码速度 {optimized_speed:.3f}x，"
            "当前电脑可实时连续处理并达到 1.1x 工程余量。"
        )
    elif realtime_capable:
        continuity_decision = "realtime_continuous"
        continuity_reason = (
            f"{continuity.reason} 编码速度 {optimized_speed:.3f}x，"
            "当前电脑可实时连续处理。"
        )
    elif speed_tier == "near_realtime":
        continuity_decision = "near_realtime_continuous"
        continuity_reason = (
            f"{continuity.reason} 编码速度 {optimized_speed:.3f}x，"
            "有限片段可近实时连续处理，但会累积少量延迟。"
        )
    else:
        continuity_decision = "offline_continuous"
        continuity_reason = (
            f"{continuity.reason} 激进模式不设速度硬门槛；"
            f"编码速度 {optimized_speed:.3f}x，只适合离线编码。"
        )
    software_continuity = FeasibilityConclusion(
        name="software_continuity",
        title="软件画面连续性",
        passed=continuity_passed,
        decision=continuity_decision,
        reason=continuity_reason,
        checks=continuity_checks,
        metrics={
            **continuity.metrics,
            "encode_speed_x": optimized_speed,
            "speed_tier": speed_tier,
        },
        thresholds={
            "min_continuous_speed_x": mode.min_speed_x,
            "realtime_speed_information_x": REALTIME_SPEED_THRESHOLD_X,
            "engineering_headroom_speed_information_x": (
                ENGINEERING_HEADROOM_THRESHOLD_X
            ),
            "max_frame_gap_in_frame_intervals": 1.5,
        },
        scope=(
            "保证输出文件画面连续；激进模式允许离线编码，保守/综合的"
            "0.97x到1.0x只适用于有限片段近实时处理。该结论不代表摄像头硬件性能。"
        ),
    )

    source_bitrate_available = bool(
        isfinite(source_video_bitrate_bps) and source_video_bitrate_bps > 0.0
    )
    source_saving = (
        calculate_saving(source_video_bitrate_bps, pair.optimized.bitrate_bps)
        if pair and source_bitrate_available
        else None
    )
    source_threshold_pass = bool(
        source_saving is not None
        and source_saving >= mode.min_source_saving_pct
    )
    deployment_passed = pair_found and source_bitrate_available and source_threshold_pass
    if not pair:
        deployment_decision = "passthrough"
        deployment_reason = "没有等画质优化候选，保持原码流直通。"
    elif not source_bitrate_available:
        deployment_decision = "passthrough"
        deployment_reason = "输入视频流码率不可用，保持原码流直通。"
    elif deployment_passed:
        deployment_decision = "hardware_validation_recommended"
        deployment_reason = (
            f"优化组合相对输入源流节省 {source_saving:.2f}%，"
            f"达到{mode.title} {mode.min_source_saving_pct:.2f}% 的源流门槛，"
            "值得进入摄像头实机验证。"
        )
    else:
        deployment_decision = "passthrough"
        deployment_reason = (
            f"优化组合相对输入源流节省 {source_saving:.2f}%，"
            f"未达到{mode.title} {mode.min_source_saving_pct:.2f}% 的源流门槛，"
            "保持原码流直通。"
        )
    deployment = FeasibilityConclusion(
        name="deployment",
        title="部署可行性初筛",
        passed=deployment_passed,
        decision=deployment_decision,
        reason=deployment_reason,
        checks={
            "equal_quality_pair_found": pair_found,
            "source_bitrate_available": source_bitrate_available,
            "mode_source_saving_threshold": source_threshold_pass,
        },
        metrics={
            "source_bitrate_bps": (
                source_video_bitrate_bps if source_bitrate_available else None
            ),
            "optimized_bitrate_bps": pair.optimized.bitrate_bps if pair else None,
            "source_saving_pct": source_saving,
        },
        thresholds={"min_source_saving_pct": mode.min_source_saving_pct},
        scope="只是摄像头实机验证的初筛，不代表已经完成硬件部署验证。",
    )
    return FeasibilityConclusions(
        algorithm=algorithm,
        software_continuity=software_continuity,
        deployment=deployment,
    )
