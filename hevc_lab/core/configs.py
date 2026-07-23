from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from .models import (
    AdaptiveQuantizationSettings,
    ComparisonPlan,
    DenoisePolicy,
    DenoiseStrength,
    EncoderConditions,
    InterConfig,
    ModePolicy,
    ROIQuantizationPolicy,
)


MODE_NAMES: Tuple[str, str, str] = ("conservative", "balanced", "aggressive")


def available_modes() -> Tuple[str, str, str]:
    return MODE_NAMES


def default_aq_profile() -> AdaptiveQuantizationSettings:
    """显式描述 medium preset 当前使用的 AQ2 对照。"""
    return AdaptiveQuantizationSettings(
        name="default",
        title="默认自动方差 AQ",
        description="x265 medium 的 AQ2、强度 1.0、32 像素量化组对照。",
        aq_mode=2,
        aq_strength=1.0,
        qg_size=32,
    )


def aq_profiles_for_mode(mode: str = "balanced") -> Tuple[AdaptiveQuantizationSettings, ...]:
    """返回当前帕累托模式下要与默认 AQ2 独立比较的候选。"""
    if mode not in MODE_NAMES:
        get_mode_policy(mode)
    strength, qg_size = {
        "conservative": (0.8, 32),
        "balanced": (1.0, 16),
        "aggressive": (1.2, 16),
    }[mode]
    return (
        AdaptiveQuantizationSettings(
            name="dark",
            title="暗场偏置 AQ",
            description="AQ3：在自动方差基础上偏向暗场，降低暗部色带和块效应风险。",
            aq_mode=3,
            aq_strength=strength,
            qg_size=qg_size,
        ),
        AdaptiveQuantizationSettings(
            name="edge",
            title="边缘信息 AQ",
            description="AQ4：在自动方差基础上加入边缘信息，研究结构细节的码率重分配。",
            aq_mode=4,
            aq_strength=strength,
            qg_size=qg_size,
        ),
    )


def roi_quantization_policy(mode: str = "balanced") -> ROIQuantizationPolicy:
    """返回固定 ROI 的三模式 QP 偏移，作为全项目唯一策略源。"""
    if mode not in MODE_NAMES:
        get_mode_policy(mode)
    values = {
        "conservative": (-2, -2, 1, 3),
        "balanced": (-3, -2, 3, 5),
        "aggressive": (-4, -3, 5, 8),
    }[mode]
    return ROIQuantizationPolicy(
        mode=mode,
        critical=values[0],
        evidence=values[1],
        normal=values[2],
        discard=values[3],
    )


def denoise_policy_for_mode(mode: str = "balanced") -> DenoisePolicy:
    """返回 ROI 保护的 hqdn3d 三模式区域强度。"""
    if mode not in MODE_NAMES:
        get_mode_policy(mode)
    values = {
        "conservative": {
            "critical": (0.2, 0.15, 0.3, 0.225),
            "normal": (0.8, 0.6, 1.2, 0.9),
            "discard": (1.2, 0.9, 1.8, 1.35),
        },
        "balanced": {
            "critical": (0.4, 0.3, 0.6, 0.45),
            "normal": (1.2, 0.9, 2.0, 1.5),
            "discard": (1.8, 1.35, 3.0, 2.25),
        },
        "aggressive": {
            "critical": (0.6, 0.45, 1.0, 0.75),
            "normal": (1.8, 1.35, 3.0, 2.25),
            "discard": (2.6, 1.95, 4.0, 3.0),
        },
    }[mode]

    def strength(role: str) -> DenoiseStrength:
        return DenoiseStrength(*values[role])

    return DenoisePolicy(
        mode=mode,
        critical=strength("critical"),
        evidence=DenoiseStrength(0.0, 0.0, 0.0, 0.0),
        normal=strength("normal"),
        discard=strength("discard"),
    )


def _mode_definitions() -> Dict[str, Tuple[ModePolicy, InterConfig]]:
    return {
        "conservative": (
            ModePolicy(
                name="conservative",
                title="保守模式",
                description="画质、随机访问和恢复能力优先，只做温和的帧间增强。",
                priority="quality_and_recovery",
                preset="medium",
                default_crf=20.0,
                target_vmaf=95.0,
                target_vmaf_p5=93.0,
                target_ssim=0.990,
                min_speed_x=0.97,
                min_algorithm_saving_pct=0.0,
                min_source_saving_pct=5.0,
                vbv_peak_ratio=2.0,
                vbv_buffer_seconds=4.0,
            ),
            InterConfig(
                name="optimized",
                title="保守模式帧间优化 H.265",
                description="保持 2 秒最大 GOP，小幅增加参考帧和前向分析深度。",
                ref=4,
                bframes=4,
                b_adapt=2,
                lookahead=30,
                gop_seconds=2,
                min_gop_seconds=1,
                scenecut=40,
                cutree=1,
                weightp=1,
            ),
        ),
        "balanced": (
            ModePolicy(
                name="balanced",
                title="综合模式",
                description="在画质、码率、编码速度和 GOP 恢复间隔之间取平衡。",
                priority="balanced",
                preset="medium",
                default_crf=22.0,
                target_vmaf=90.0,
                target_vmaf_p5=88.0,
                target_ssim=0.980,
                min_speed_x=0.97,
                min_algorithm_saving_pct=5.0,
                min_source_saving_pct=5.0,
                vbv_peak_ratio=1.5,
                vbv_buffer_seconds=3.0,
            ),
            InterConfig(
                name="optimized",
                title="综合模式帧间优化 H.265",
                description="加深参考帧、B 帧和前向分析，并将最大 GOP 延长到 4 秒。",
                ref=5,
                bframes=6,
                b_adapt=2,
                lookahead=60,
                gop_seconds=4,
                min_gop_seconds=2,
                scenecut=40,
                cutree=1,
                weightp=1,
            ),
        ),
        "aggressive": (
            ModePolicy(
                name="aggressive",
                title="激进模式",
                description="码率节省优先，使用 slow 并允许离线编码。",
                priority="bitrate_saving",
                preset="slow",
                default_crf=24.0,
                target_vmaf=83.0,
                target_vmaf_p5=80.0,
                target_ssim=0.950,
                min_speed_x=None,
                min_algorithm_saving_pct=10.0,
                min_source_saving_pct=10.0,
                vbv_peak_ratio=1.25,
                vbv_buffer_seconds=2.0,
            ),
            InterConfig(
                name="optimized",
                title="激进模式帧间优化 H.265",
                description="使用更多参考帧、B 帧、更深前向分析和 10 秒最大 GOP。",
                ref=6,
                bframes=8,
                b_adapt=2,
                lookahead=90,
                gop_seconds=10,
                min_gop_seconds=2,
                scenecut=40,
                cutree=1,
                weightp=1,
            ),
        ),
    }


def get_mode_policy(mode: str = "balanced") -> ModePolicy:
    try:
        return _mode_definitions()[mode][0]
    except KeyError as exc:
        choices = "、".join(available_modes())
        raise ValueError(f"未知模式 {mode!r}，可选：{choices}") from exc


def resolve_encoder_conditions(
    conditions: EncoderConditions,
    preset_override: Optional[str] = None,
) -> EncoderConditions:
    """解析模式默认 preset 与专家覆盖，并保留报告来源。"""
    if preset_override is None:
        return conditions
    if not preset_override.strip():
        raise ValueError("preset 覆盖值不能为空")
    return replace(
        conditions,
        preset=preset_override,
        preset_source="override",
        mode_default_preset=conditions.mode_default_preset or conditions.preset,
    )


def v1_comparison_plan(mode: str = "balanced") -> ComparisonPlan:
    """返回指定帕累托模式下的两套正式比较模型。

    编码器、preset、profile 和像素格式只保存一份，避免两路
    实验的非帧间条件意外分叉。
    """
    definitions = _mode_definitions()
    if mode not in definitions:
        get_mode_policy(mode)
    policy, optimized = definitions[mode]
    conditions = EncoderConditions(
        encoder="libx265",
        preset=policy.preset,
        profile="main",
        pixel_format="yuv420p",
        preset_source="mode",
        mode_default_preset=policy.preset,
    )
    baseline = InterConfig(
        name="baseline",
        title="工程基线 H.265",
        description="2 秒最大 GOP，作为同条件 libx265 工程基线。",
        ref=3,
        bframes=4,
        b_adapt=2,
        lookahead=20,
        gop_seconds=2,
        min_gop_seconds=1,
        scenecut=40,
        cutree=1,
        weightp=1,
    )
    return ComparisonPlan(
        mode=policy,
        conditions=conditions,
        baseline=baseline,
        optimized=optimized,
    )


def interframe_configs(mode: str = "balanced") -> List[InterConfig]:
    """兼容现有实验编排器，仅返回 V1.0 的两套正式模型。"""
    return list(v1_comparison_plan(mode).schemes)
