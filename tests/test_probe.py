import tempfile
import unittest
from pathlib import Path

from hevc_lab.probe import (
    packet_bitrate_stats_from_probe,
    parse_fraction,
    video_info_from_probe,
)
from hevc_lab.errors import VideoError


class ProbeTests(unittest.TestCase):
    def test_fraction(self):
        self.assertAlmostEqual(parse_fraction("30000/1001"), 29.97002997, places=6)
        self.assertEqual(parse_fraction("0/0"), 0.0)

    def test_fallback_bitrate_from_size_and_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "video.bin"
            path.write_bytes(b"x" * 1000)
            payload = {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "hevc",
                        "width": 100,
                        "height": 50,
                        "avg_frame_rate": "25/1",
                        "pix_fmt": "yuv420p",
                    }
                ],
                "format": {"duration": "10", "size": "1000"},
            }
            info = video_info_from_probe(path, payload)
            self.assertEqual(info.video_bitrate_bps, 800.0)

    def test_stream_bitrate_has_priority(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "video.bin"
            path.write_bytes(b"x" * 1000)
            payload = {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "hevc",
                        "width": 100,
                        "height": 50,
                        "avg_frame_rate": "25/1",
                        "pix_fmt": "yuv420p",
                        "bit_rate": "123456",
                    }
                ],
                "format": {"duration": "10", "size": "1000", "bit_rate": "999999"},
            }
            info = video_info_from_probe(path, payload)
            self.assertEqual(info.video_bitrate_bps, 123456.0)

    def test_packet_bytes_define_average_and_one_second_peak(self):
        stats = packet_bitrate_stats_from_probe(
            {
                "packets": [
                    {"pts_time": "0.0", "size": "500"},
                    {"pts_time": "0.5", "size": "500"},
                    {"pts_time": "1.2", "size": "1000"},
                ]
            },
            duration_seconds=2.0,
            window_seconds=1.0,
        )
        self.assertEqual(stats.packet_count, 3)
        self.assertEqual(stats.packet_bytes, 2000)
        self.assertEqual(stats.average_bitrate_bps, 8000)
        self.assertEqual(stats.window_bitrates_bps, (8000, 8000))
        self.assertEqual(stats.peak_window_bitrate_bps, 8000)

    def test_packet_stats_reject_empty_video_packets(self):
        with self.assertRaises(VideoError):
            packet_bitrate_stats_from_probe(
                {"packets": [{"size": "0"}]},
                duration_seconds=1.0,
            )


if __name__ == "__main__":
    unittest.main()
