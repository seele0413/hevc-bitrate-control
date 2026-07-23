from pathlib import Path

from ..core.models import Toolchain
from ..errors import LabError
from ..tools import run_process


def generate_sample(toolchain: Toolchain, output_path: Path) -> Path:
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        "[0:v]format=yuv420p[s0];"
        "[1:v]format=yuv420p[s1];"
        "[2:v]format=yuv420p[s2];"
        "[s0][s1][s2]concat=n=3:v=1:a=0[v]"
    )
    run_process(
        [
            toolchain.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=640x360:rate=25:d=4",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=25:d=4",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=640x360:rate=25:d=4",
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-c:v",
            "ffv1",
            output_path,
        ]
    )
    if not output_path.is_file():
        raise LabError("测试视频生成失败")
    return output_path
