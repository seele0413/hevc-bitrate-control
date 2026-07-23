import argparse
import sys
from pathlib import Path

from . import __version__
from .core.configs import available_modes
from .errors import LabError
from .experiment import generate_sample, run_experiment
from .quality_search import run_single_quality_search
from .pair_search import run_pair_quality_search
from .comparison import run_comparison
from .rate_control_study import run_rate_control_study
from .aq_study import run_aq_study
from .roi_study import run_roi_study
from .denoise_study import run_denoise_study
from .preset_study import run_preset_study
from .multi_encode import run_multi_encode
from .tools import check_capabilities, discover_toolchain


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hevc_lab",
        description="H.265 帧间预测效率实验工具",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-env", help="检查 Python 外部工具链")

    sample = subparsers.add_parser("generate-sample", help="生成静态+运动的无损测试视频")
    sample.add_argument("--output", type=Path, required=True)

    multi_encode = subparsers.add_parser(
        "multi-encode",
        help="生成 x265 默认方案和保守/均衡/激进三档综合策略",
    )
    multi_encode.add_argument("--input", type=Path, required=True)
    multi_encode.add_argument("--roi-config", type=Path, required=True)
    multi_encode.add_argument("--output", type=Path, required=True)

    web = subparsers.add_parser("web", help="启动本机 Web 界面")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)

    experiment = subparsers.add_parser("experiment", help="执行帧间预测参数对照实验")
    experiment.add_argument("--input", type=Path, required=True)
    experiment.add_argument("--output", type=Path, required=True)
    experiment.add_argument("--start", type=float, default=0.0, help="参考片段开始时间，单位秒")
    experiment.add_argument("--duration", type=float, default=15.0, help="参考片段时长，默认 15 秒")
    experiment.add_argument(
        "--mode",
        choices=available_modes(),
        default="balanced",
        help="帕累托模式：conservative、balanced（默认）或 aggressive",
    )
    experiment.add_argument("--crf", type=float, default=None, help="覆盖模式默认 CRF")
    experiment.add_argument("--preset", default=None, help="覆盖模式默认 preset")
    experiment.add_argument("--target-vmaf", type=float, default=None, help="覆盖模式默认 VMAF 门槛")
    experiment.add_argument("--target-vmaf-p5", type=float, default=None, help="覆盖模式默认 VMAF P5 门槛")
    experiment.add_argument("--target-ssim", type=float, default=None, help="覆盖模式默认 SSIM 门槛")
    experiment.add_argument("--min-speed", type=float, default=None, help="覆盖模式默认速度门槛")
    experiment.add_argument(
        "--min-algorithm-saving",
        type=float,
        default=None,
        help="覆盖模式默认的相对工程基线节省门槛",
    )
    experiment.add_argument(
        "--min-source-saving",
        type=float,
        default=None,
        help="覆盖模式默认的源流节省门槛",
    )

    search = subparsers.add_parser(
        "search-crf",
        help="对单个 baseline 或 optimized 方案执行自适应画质 CRF 搜索",
    )
    search.add_argument("--input", type=Path, required=True)
    search.add_argument("--output", type=Path, required=True)
    search.add_argument("--start", type=float, default=0.0, help="参考片段开始时间，单位秒")
    search.add_argument("--duration", type=float, default=15.0, help="参考片段时长，默认 15 秒")
    search.add_argument(
        "--mode",
        choices=available_modes(),
        default="balanced",
        help="帕累托模式：conservative、balanced（默认）或 aggressive",
    )
    search.add_argument(
        "--scheme",
        choices=("baseline", "optimized"),
        default="optimized",
        help="本轮只搜索一个方案，默认 optimized",
    )
    search.add_argument("--preset", default=None, help="覆盖模式默认 preset")
    search.add_argument("--target-vmaf", type=float, default=None, help="覆盖模式默认 VMAF 门槛")
    search.add_argument("--target-vmaf-p5", type=float, default=None, help="覆盖模式默认 VMAF P5 门槛")
    search.add_argument("--target-ssim", type=float, default=None, help="覆盖模式默认 SSIM 门槛")
    search.add_argument("--min-speed", type=float, default=None, help="覆盖模式默认速度门槛")

    pair = subparsers.add_parser(
        "pair-crf",
        help="独立搜索 baseline/optimized 并执行肉眼无损近似配对",
    )
    pair.add_argument("--input", type=Path, required=True)
    pair.add_argument("--output", type=Path, required=True)
    pair.add_argument("--start", type=float, default=0.0, help="参考片段开始时间，单位秒")
    pair.add_argument("--duration", type=float, default=15.0, help="参考片段时长，默认 15 秒")
    pair.add_argument(
        "--mode",
        choices=available_modes(),
        default="balanced",
        help="帕累托模式：conservative、balanced（默认）或 aggressive",
    )
    pair.add_argument("--preset", default=None, help="覆盖模式默认 preset")
    pair.add_argument("--target-vmaf", type=float, default=None, help="覆盖模式默认 VMAF 门槛")
    pair.add_argument("--target-vmaf-p5", type=float, default=None, help="覆盖模式默认 VMAF P5 门槛")
    pair.add_argument("--target-ssim", type=float, default=None, help="覆盖模式默认 SSIM 门槛")
    pair.add_argument("--min-speed", type=float, default=None, help="覆盖模式默认速度门槛")
    pair.add_argument(
        "--max-vmaf-delta",
        type=float,
        default=1.0,
        help="肉眼无损近似配对要求 |ΔVMAF| 不超过该值，默认 1.0",
    )

    compare = subparsers.add_parser(
        "compare",
        help="执行正式双方案比较、连续性检查、三结论和可恢复缓存",
    )
    compare.add_argument("--input", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--start", type=float, default=0.0)
    compare.add_argument("--duration", type=float, default=15.0)
    compare.add_argument(
        "--mode",
        choices=available_modes(),
        default="balanced",
    )
    compare.add_argument("--preset", default=None)
    compare.add_argument("--target-vmaf", type=float, default=None)
    compare.add_argument("--target-vmaf-p5", type=float, default=None)
    compare.add_argument("--target-ssim", type=float, default=None)
    compare.add_argument("--min-speed", type=float, default=None)
    compare.add_argument(
        "--min-saving",
        type=float,
        default=None,
        help="同时覆盖算法和源流节省门槛；独立参数优先",
    )
    compare.add_argument("--min-algorithm-saving", type=float, default=None)
    compare.add_argument("--min-source-saving", type=float, default=None)
    compare.add_argument("--max-vmaf-delta", type=float, default=1.0)

    rate_control = subparsers.add_parser(
        "rate-control",
        help="执行质量驱动 CRF + VBV 峰值保护实验",
    )
    rate_control.add_argument("--input", type=Path, required=True)
    rate_control.add_argument("--output", type=Path, required=True)
    rate_control.add_argument("--start", type=float, default=0.0)
    rate_control.add_argument("--duration", type=float, default=15.0)
    rate_control.add_argument(
        "--mode",
        choices=available_modes(),
        default="balanced",
    )
    rate_control.add_argument(
        "--scheme",
        choices=("baseline", "optimized"),
        default="optimized",
    )
    rate_control.add_argument("--preset", default=None)
    rate_control.add_argument("--target-vmaf", type=float, default=None)
    rate_control.add_argument("--target-vmaf-p5", type=float, default=None)
    rate_control.add_argument("--target-ssim", type=float, default=None)
    rate_control.add_argument("--min-speed", type=float, default=None)
    rate_control.add_argument("--max-vmaf-delta", type=float, default=1.0)
    rate_control.add_argument(
        "--maximum-peak-ratio",
        type=float,
        default=2.5,
        help="画质不通过时允许自动放宽到的最大峰值倍率",
    )

    preset_study = subparsers.add_parser(
        "preset-study",
        help="固定其余条件，独立搜索 x265 medium/slow 并执行等画质配对",
    )
    preset_study.add_argument("--input", type=Path, required=True)
    preset_study.add_argument("--output", type=Path, required=True)
    preset_study.add_argument("--start", type=float, default=0.0)
    preset_study.add_argument("--duration", type=float, default=15.0)
    preset_study.add_argument(
        "--mode",
        choices=available_modes(),
        default="aggressive",
    )
    preset_study.add_argument(
        "--scheme",
        choices=("baseline", "optimized"),
        default="optimized",
    )
    preset_study.add_argument("--target-vmaf", type=float, default=None)
    preset_study.add_argument("--target-vmaf-p5", type=float, default=None)
    preset_study.add_argument("--target-ssim", type=float, default=None)
    preset_study.add_argument("--min-speed", type=float, default=None)
    preset_study.add_argument("--max-vmaf-delta", type=float, default=1.0)

    aq_study = subparsers.add_parser(
        "aq-study",
        help="独立搜索默认、暗场和边缘 AQ，并执行等画质配对",
    )
    aq_study.add_argument("--input", type=Path, required=True)
    aq_study.add_argument("--output", type=Path, required=True)
    aq_study.add_argument("--start", type=float, default=0.0)
    aq_study.add_argument("--duration", type=float, default=15.0)
    aq_study.add_argument(
        "--mode",
        choices=available_modes(),
        default="balanced",
    )
    aq_study.add_argument(
        "--scheme",
        choices=("baseline", "optimized"),
        default="optimized",
    )
    aq_study.add_argument("--preset", default=None)
    aq_study.add_argument("--target-vmaf", type=float, default=None)
    aq_study.add_argument("--target-vmaf-p5", type=float, default=None)
    aq_study.add_argument("--target-ssim", type=float, default=None)
    aq_study.add_argument("--min-speed", type=float, default=None)
    aq_study.add_argument("--max-vmaf-delta", type=float, default=1.0)

    roi_study = subparsers.add_parser(
        "roi-study",
        help="独立搜索无 ROI/ROI 画质边界并检查重点区域",
    )
    roi_study.add_argument("--input", type=Path, required=True)
    roi_study.add_argument("--roi-config", type=Path, required=True)
    roi_study.add_argument("--output", type=Path, required=True)
    roi_study.add_argument("--start", type=float, default=0.0)
    roi_study.add_argument("--duration", type=float, default=15.0)
    roi_study.add_argument(
        "--mode",
        choices=available_modes(),
        default="balanced",
    )
    roi_study.add_argument(
        "--scheme",
        choices=("baseline", "optimized"),
        default="optimized",
    )
    roi_study.add_argument("--preset", default=None)
    roi_study.add_argument("--target-vmaf", type=float, default=None)
    roi_study.add_argument("--target-vmaf-p5", type=float, default=None)
    roi_study.add_argument("--target-ssim", type=float, default=None)
    roi_study.add_argument("--min-speed", type=float, default=None)
    roi_study.add_argument("--max-vmaf-delta", type=float, default=1.0)

    denoise_study = subparsers.add_parser(
        "denoise-study",
        help="独立搜索无降噪/ROI 保护降噪的画质边界",
    )
    denoise_study.add_argument("--input", type=Path, required=True)
    denoise_study.add_argument("--roi-config", type=Path, required=True)
    denoise_study.add_argument("--output", type=Path, required=True)
    denoise_study.add_argument("--start", type=float, default=0.0)
    denoise_study.add_argument("--duration", type=float, default=15.0)
    denoise_study.add_argument(
        "--mode",
        choices=available_modes(),
        default="balanced",
    )
    denoise_study.add_argument(
        "--scheme",
        choices=("baseline", "optimized"),
        default="optimized",
    )
    denoise_study.add_argument("--preset", default=None)
    denoise_study.add_argument("--target-vmaf", type=float, default=None)
    denoise_study.add_argument("--target-vmaf-p5", type=float, default=None)
    denoise_study.add_argument("--target-ssim", type=float, default=None)
    denoise_study.add_argument("--min-speed", type=float, default=None)
    denoise_study.add_argument("--max-vmaf-delta", type=float, default=1.0)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        toolchain = discover_toolchain()
        if args.command == "check-env":
            info = check_capabilities(toolchain)
            print("环境检查通过")
            print(f"FFmpeg：{info['ffmpeg']}")
            print(f"FFprobe：{info['ffprobe']}")
            print("libx265：可用")
            print("libx264：可用")
            print("libvmaf：可用")
            print("addroi：可用")
            print("hqdn3d：可用")
            print(f"VMAF 模型：{info['vmaf_model']}")
            return 0
        if args.command == "generate-sample":
            output = generate_sample(toolchain, args.output)
            print(f"测试视频已生成：{output}")
            return 0
        if args.command == "multi-encode":
            payload = run_multi_encode(
                toolchain=toolchain,
                input_path=args.input,
                roi_config_path=args.roi_config,
                output_dir=args.output,
            )
            print("四路编码完成：")
            for strategy in payload["strategies"]:
                if strategy["status"] != "completed":
                    print(
                        f"- {strategy['title']}：未生成；"
                        f"{strategy.get('failure_reason', '未知原因')}"
                    )
                    continue
                bitrate = strategy["average_video_packet_bitrate_mbps"]
                saving = strategy.get("saving_vs_default_pct")
                saving_text = "—" if saving is None else f"{saving:.2f}%"
                print(
                    f"- {strategy['title']}：{strategy['resolution']}，"
                    f"{bitrate:.6f} Mbit/s，相对默认节省 {saving_text}"
                )
            print(f"结果目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "web":
            from .web import run_web_server

            run_web_server(host=args.host, port=args.port, toolchain=toolchain)
            return 0
        if args.command == "search-crf":
            payload = run_single_quality_search(
                toolchain=toolchain,
                input_path=args.input,
                output_dir=args.output,
                mode=args.mode,
                scheme_name=args.scheme,
                preset=args.preset,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                start_seconds=args.start,
                duration_seconds=args.duration,
            )
            selected = payload["search"]["selected"]
            print(f"运行模式：{payload['mode']['title']}（{args.mode}）")
            print(f"搜索方案：{payload['scheme']['title']}")
            if selected:
                print(
                    f"单方案搜索完成：满足全部画质门槛的最高点为 "
                    f"CRF {selected['crf']:.1f}"
                )
            else:
                print("单方案搜索完成：CRF 18.0～38.0 内没有合格点")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "pair-crf":
            payload = run_pair_quality_search(
                toolchain=toolchain,
                input_path=args.input,
                output_dir=args.output,
                mode=args.mode,
                preset=args.preset,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                max_vmaf_delta=args.max_vmaf_delta,
                start_seconds=args.start,
                duration_seconds=args.duration,
            )
            match = payload["match"]
            print(f"运行模式：{payload['mode']['title']}（{args.mode}）")
            if match["status"] == "matched":
                pair = match["pair"]
                print(
                    f"配对完成：baseline CRF {pair['baseline']['crf']:.1f}，"
                    f"optimized CRF {pair['optimized']['crf']:.1f}，"
                    f"|ΔVMAF|={pair['vmaf_delta']:.3f}"
                )
            else:
                print(f"证据不足：{match['reason']}")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "compare":
            payload = run_comparison(
                toolchain=toolchain,
                input_path=args.input,
                output_dir=args.output,
                mode=args.mode,
                preset=args.preset,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                min_saving=args.min_saving,
                min_algorithm_saving=args.min_algorithm_saving,
                min_source_saving=args.min_source_saving,
                max_vmaf_delta=args.max_vmaf_delta,
                start_seconds=args.start,
                duration_seconds=args.duration,
            )
            cache = payload["comparison_cache"]
            if cache["experiment_cache_hit"]:
                print("完整实验缓存命中，无需重新执行搜索和连续性检查")
            elif cache["resumed"]:
                attempt = cache["attempt"]
                print(f"已从第 {attempt} 次尝试恢复实验")
            mode_title = payload["mode"]["title"]
            print(f"运行模式：{mode_title}（{args.mode}）")
            for key in ("algorithm", "software_continuity", "deployment"):
                conclusion = payload["conclusions"][key]
                status = "通过" if conclusion["passed"] else "不通过"
                title = conclusion["title"]
                reason = conclusion["reason"]
                print(f"{title}：{status}；{reason}")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "rate-control":
            payload = run_rate_control_study(
                toolchain=toolchain,
                input_path=args.input,
                output_dir=args.output,
                mode=args.mode,
                scheme_name=args.scheme,
                preset=args.preset,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                max_vmaf_delta=args.max_vmaf_delta,
                maximum_peak_ratio=args.maximum_peak_ratio,
                start_seconds=args.start,
                duration_seconds=args.duration,
            )
            print(f"运行模式：{payload['mode']['title']}（{args.mode}）")
            if payload["selected"]:
                selected = payload["selected"]
                print(
                    f"码控实验完成：选择 {selected['peak_ratio']:.2f}x 峰值倍率，"
                    f"平均码率变化 {selected['average_saving_vs_uncapped_pct']:.2f}%，"
                    f"1秒峰值变化 {selected['peak_saving_vs_uncapped_pct']:.2f}%"
                )
            else:
                print("码控实验完成：没有候选同时保持画质并改善码率，回退无上限 CRF")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "aq-study":
            payload = run_aq_study(
                toolchain=toolchain,
                input_path=args.input,
                output_dir=args.output,
                mode=args.mode,
                scheme_name=args.scheme,
                preset=args.preset,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                max_vmaf_delta=args.max_vmaf_delta,
                start_seconds=args.start,
                duration_seconds=args.duration,
            )
            print(f"运行模式：{payload['mode']['title']}（{args.mode}）")
            if payload["selected"]:
                selected = payload["selected"]
                pair = selected["match"]["pair"]
                print(
                    f"AQ 实验完成：选择 {selected['profile']['title']}，"
                    f"等画质平均码率变化 {pair['algorithm_saving_pct']:.2f}%，"
                    f"|ΔVMAF|={pair['vmaf_delta']:.3f}"
                )
            else:
                print("AQ 实验完成：没有候选带来等画质码率收益，回退默认 AQ2")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "preset-study":
            payload = run_preset_study(
                toolchain=toolchain,
                input_path=args.input,
                output_dir=args.output,
                mode=args.mode,
                scheme_name=args.scheme,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                max_vmaf_delta=args.max_vmaf_delta,
                start_seconds=args.start,
                duration_seconds=args.duration,
            )
            decision = payload["decision"]
            print(f"运行模式：{payload['mode']['title']}（{args.mode}）")
            if decision["benefit_confirmed"]:
                print(
                    "preset 实验完成：slow 等画质平均视频包码率降低 "
                    f"{decision['saving_pct']:.2f}%"
                )
            else:
                print(f"preset 实验完成：{decision['reason']}")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "roi-study":
            payload = run_roi_study(
                toolchain=toolchain,
                input_path=args.input,
                roi_config_path=args.roi_config,
                output_dir=args.output,
                mode=args.mode,
                scheme_name=args.scheme,
                preset=args.preset,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                max_vmaf_delta=args.max_vmaf_delta,
                start_seconds=args.start,
                duration_seconds=args.duration,
            )
            print(f"运行模式：{payload['mode']['title']}（{args.mode}）")
            if payload["decision"]["selected"]:
                pair = payload["match"]["pair"]
                print(
                    f"ROI 实验完成：选中静态 ROI，"
                    f"等画质平均码率降低 {pair['algorithm_saving_pct']:.2f}%"
                )
            else:
                reasons = "；".join(payload["decision"]["reasons"])
                print(f"ROI 实验完成：回退无 ROI AQ2 对照。{reasons}")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "denoise-study":
            payload = run_denoise_study(
                toolchain=toolchain,
                input_path=args.input,
                roi_config_path=args.roi_config,
                output_dir=args.output,
                mode=args.mode,
                scheme_name=args.scheme,
                preset=args.preset,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                max_vmaf_delta=args.max_vmaf_delta,
                start_seconds=args.start,
                duration_seconds=args.duration,
            )
            print(f"运行模式：{payload['mode']['title']}（{args.mode}）")
            if payload["decision"]["selected"]:
                pair = payload["match"]["pair"]
                print(
                    f"降噪实验完成：选中 ROI 保护降噪，"
                    f"等画质平均码率降低 {pair['algorithm_saving_pct']:.2f}%"
                )
            else:
                reasons = "；".join(payload["decision"]["reasons"])
                print(f"降噪实验完成：回退无降噪 AQ2 对照。{reasons}")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
        if args.command == "experiment":
            payload = run_experiment(
                toolchain=toolchain,
                input_path=args.input,
                output_dir=args.output,
                crf=args.crf,
                preset=args.preset,
                target_vmaf=args.target_vmaf,
                target_vmaf_p5=args.target_vmaf_p5,
                target_ssim=args.target_ssim,
                min_speed=args.min_speed,
                min_algorithm_saving=args.min_algorithm_saving,
                min_source_saving=args.min_source_saving,
                start_seconds=args.start,
                duration_seconds=args.duration,
                mode=args.mode,
            )
            mode_title = payload["settings"]["mode"]["title"]
            print(f"运行模式：{mode_title}（{args.mode}）")
            if payload["selected"]:
                selected = payload["selected"]
                print(
                    f"实验完成：选中 {selected['title']}，"
                    f"较基线节省 {selected['bitrate_saving_vs_baseline_pct']:.2f}%"
                )
            else:
                best = payload.get("best_interframe_candidate")
                if best:
                    print(
                        f"参数研究完成：最佳候选 {best['title']}，"
                        f"较基线节省 {best['bitrate_saving_vs_baseline_pct']:.2f}%"
                    )
                    print(
                        "部署决定：保持原码流直通；候选没有同时满足画质、速度、算法节省和源流节省门槛"
                    )
                else:
                    print("实验完成：没有候选通过画质与速度门槛，保持原码流直通")
            print(f"报告目录：{args.output.expanduser().resolve()}")
            return 0
    except (LabError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 1
