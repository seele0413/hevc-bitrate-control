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
    MultiEncodeStrategy,
    ROIQuantizationPolicy,
)


MODE_NAMES: Tuple[str, ...] = (
    "conservative",
    "balanced",
    "aggressive",
    "aggressive_plus",
    "aggressive_plus_plus",
    "aggressive_plus_plus_plus",
)

MULTI_ENCODE_MODE_NAMES: Tuple[str, str, str] = (
    "general",
    "roi",
    "roi_denoise",
)


def available_modes() -> Tuple[str, ...]:
    return MODE_NAMES


def multi_encode_modes() -> Tuple[str, str, str]:
    return MULTI_ENCODE_MODE_NAMES


def multi_encode_strategies() -> Tuple[MultiEncodeStrategy, ...]:
    """返回 V1.4 正式四路中的三个非默认公开策略。"""
    return (
        MultiEncodeStrategy(
            public_mode="general",
            strategy_id="generic_no_roi",
            title="通用无 ROI 方案",
            description=(
                "预算基准方案：基于 V1.0 原激进帧间结构，"
                "使用 medium preset，不使用静态 ROI QP 或 ROI 分区降噪。"
            ),
            source_mode="aggressive",
            strategy_generation="v1.4_general_no_roi_budget",
            effective_preset="medium",
            roi_enabled=False,
            denoise_enabled=False,
            target_vmaf=83.0,
            target_vmaf_p5=80.0,
            target_ssim=0.950,
            crf_search_max=38.0,
        ),
        MultiEncodeStrategy(
            public_mode="roi",
            strategy_id="budget_neutral_roi",
            title="预算中性 ROI 方案",
            description=(
                "在通用无 ROI 方案的平均视频包码率预算内，"
                "仅使用静态 ROI QP 重新分配码率。"
            ),
            source_mode="aggressive",
            strategy_generation="v1.4_budget_neutral_roi",
            effective_preset="medium",
            roi_enabled=True,
            denoise_enabled=False,
            target_vmaf=83.0,
            target_vmaf_p5=80.0,
            target_ssim=0.950,
            crf_search_max=38.0,
            budget_reference="generic_no_roi",
            budget_neutral_required=True,
            roi_quality_required=True,
        ),
        MultiEncodeStrategy(
            public_mode="roi_denoise",
            strategy_id="roi_denoise_experimental",
            title="ROI + 降噪实验项",
            description=(
                "在通用无 ROI 方案的平均视频包码率预算内，"
                "叠加静态 ROI QP 与 ROI 保护分区降噪。"
            ),
            source_mode="aggressive",
            strategy_generation="v1.4_roi_denoise_experimental",
            effective_preset="medium",
            roi_enabled=True,
            denoise_enabled=True,
            target_vmaf=83.0,
            target_vmaf_p5=80.0,
            target_ssim=0.950,
            crf_search_max=38.0,
            budget_reference="generic_no_roi",
            budget_neutral_required=True,
            roi_quality_required=True,
            experimental=True,
        ),
    )


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
        "aggressive_plus": (1.2, 16),
        "aggressive_plus_plus": (1.2, 16),
        "aggressive_plus_plus_plus": (1.2, 16),
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
        "conservative": (-1, -2, 4, 6),
        "balanced": (-2, -2, 5, 8),
        "aggressive": (-4, -3, 5, 8),
        "aggressive_plus": (-4, -3, 7, 12),
        "aggressive_plus_plus": (-4, -3, 9, 16),
        "aggressive_plus_plus_plus": (-4, -3, 11, 20),
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
            "critical": (0.3, 0.225, 0.45, 0.35),
            "normal": (1.2, 0.9, 2.0, 1.5),
            "discard": (2.0, 1.5, 3.4, 2.5),
        },
        "balanced": {
            "critical": (0.4, 0.3, 0.6, 0.45),
            "normal": (1.6, 1.2, 2.8, 2.1),
            "discard": (2.4, 1.8, 4.0, 3.0),
        },
        "aggressive": {
            "critical": (0.6, 0.45, 1.0, 0.75),
            "normal": (1.8, 1.35, 3.0, 2.25),
            "discard": (2.6, 1.95, 4.0, 3.0),
        },
        "aggressive_plus": {
            "critical": (0.7, 0.525, 1.2, 0.9),
            "normal": (2.2, 1.65, 3.6, 2.7),
            "discard": (3.2, 2.4, 5.0, 3.75),
        },
        "aggressive_plus_plus": {
            "critical": (0.8, 0.6, 1.4, 1.05),
            "normal": (2.6, 1.95, 4.4, 3.3),
            "discard": (3.8, 2.85, 6.0, 4.5),
        },
        "aggressive_plus_plus_plus": {
            "critical": (0.9, 0.675, 1.6, 1.2),
            "normal": (3.0, 2.25, 5.2, 3.9),
            "discard": (4.4, 3.3, 7.0, 5.25),
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
                target_vmaf=90.0,
                target_vmaf_p5=88.0,
                target_ssim=0.980,
                min_speed_x=0.97,
                min_algorithm_saving_pct=0.0,
                min_source_saving_pct=5.0,
                vbv_peak_ratio=2.0,
                vbv_buffer_seconds=4.0,
                target_saving_min_pct=10.0,
                target_saving_max_pct=15.0,
            ),
            InterConfig(
                name="optimized",
                title="保守模式帧间优化 H.265",
                description="延长到 8 秒最大 GOP，小幅增加 B 帧和前向分析深度。",
                ref=4,
                bframes=5,
                b_adapt=2,
                lookahead=45,
                gop_seconds=8,
                min_gop_seconds=2,
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
                target_saving_min_pct=20.0,
                target_saving_max_pct=30.0,
            ),
            InterConfig(
                name="optimized",
                title="综合模式帧间优化 H.265",
                description="加深参考帧、B 帧和前向分析，并将最大 GOP 延长到 10 秒。",
                ref=5,
                bframes=6,
                b_adapt=2,
                lookahead=60,
                gop_seconds=10,
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
        "aggressive_plus": (
            ModePolicy(
                name="aggressive_plus",
                title="激进+模式",
                description="在原激进 slow preset 基础上延长 GOP、加深前向分析，并提高 CRF 搜索上限。",
                priority="bitrate_saving_plus",
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
                crf_search_max=42.0,
            ),
            InterConfig(
                name="optimized",
                title="激进+模式综合 H.265",
                description="slow preset、6 参考帧、8 B 帧、100 帧 lookahead 和 12 秒最大 GOP。",
                ref=6,
                bframes=8,
                b_adapt=2,
                lookahead=100,
                gop_seconds=12,
                min_gop_seconds=2,
                scenecut=40,
                cutree=1,
                weightp=1,
            ),
        ),
        "aggressive_plus_plus": (
            ModePolicy(
                name="aggressive_plus_plus",
                title="激进++模式",
                description="继续延长 GOP 与 lookahead，并配合更强背景 ROI QP 和降噪。",
                priority="bitrate_saving_plus_plus",
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
                crf_search_max=45.0,
            ),
            InterConfig(
                name="optimized",
                title="激进++模式综合 H.265",
                description="slow preset、6 参考帧、8 B 帧、120 帧 lookahead 和 15 秒最大 GOP。",
                ref=6,
                bframes=8,
                b_adapt=2,
                lookahead=120,
                gop_seconds=15,
                min_gop_seconds=2,
                scenecut=40,
                cutree=1,
                weightp=1,
            ),
        ),
        "aggressive_plus_plus_plus": (
            ModePolicy(
                name="aggressive_plus_plus_plus",
                title="激进+++模式",
                description="V1.2 最高压缩探索档：最长 GOP、最深 lookahead 和最强背景压缩。",
                priority="bitrate_saving_plus_plus_plus",
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
                crf_search_max=48.0,
            ),
            InterConfig(
                name="optimized",
                title="激进+++模式综合 H.265",
                description="slow preset、6 参考帧、8 B 帧、150 帧 lookahead 和 20 秒最大 GOP。",
                ref=6,
                bframes=8,
                b_adapt=2,
                lookahead=150,
                gop_seconds=20,
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
