"""输入来源与外部介质适配边界。"""

from .sample import generate_sample
from .reference import prepare_reference
from .continuity import verify_video_continuity
from .video_input import (
    packet_bitrate_stats_from_probe,
    parse_fraction,
    probe_frame_timestamps,
    probe_video,
    probe_video_packet_stats,
    video_info_from_probe,
)

__all__ = [
    "generate_sample",
    "packet_bitrate_stats_from_probe",
    "parse_fraction",
    "prepare_reference",
    "verify_video_continuity",
    "probe_frame_timestamps",
    "probe_video",
    "probe_video_packet_stats",
    "video_info_from_probe",
]
