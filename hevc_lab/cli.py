import argparse
import sys

from . import __version__
from .errors import LabError
from .tools import check_capabilities, discover_toolchain


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m hevc_lab",
        description="H.264 源码直流与 H.265 直接播放实时工具",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check-env", help="检查实时处理所需 FFmpeg 能力")
    web = subparsers.add_parser("web", help="启动本机实时 Web 界面")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        toolchain = discover_toolchain()
        if args.command == "check-env":
            info = check_capabilities(toolchain)
            print("环境检查通过")
            print(f"FFmpeg：{info['ffmpeg']}")
            print(f"FFprobe：{info['ffprobe']}")
            print("H.264 解码：可用")
            print("libx265 编码：可用")
            print("MPEG-TS 输入/输出：可用")
            print("RTSP 输入与 HLS 输出：可用")
            return 0
        if args.command == "web":
            from .web import run_web_server

            run_web_server(
                host=args.host,
                port=args.port,
                toolchain=toolchain,
            )
            return 0
    except (LabError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0
