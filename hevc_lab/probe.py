"""旧版顶层导入的兼容层；新代码应从 :mod:`hevc_lab.adapters.video_input` 导入。"""

from .adapters.video_input import (
    packet_bitrate_stats_from_probe,
    parse_fraction,
    probe_video,
    probe_video_packet_stats,
    video_info_from_probe,
)

__all__ = [
    "packet_bitrate_stats_from_probe",
    "parse_fraction",
    "probe_video",
    "probe_video_packet_stats",
    "video_info_from_probe",
]
