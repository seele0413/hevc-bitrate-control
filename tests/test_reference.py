import tempfile
import unittest
from pathlib import Path

from hevc_lab.adapters.reference import (
    build_reference_cache_key,
    normalize_clip_window,
    sha256_file,
    summarize_timestamps,
)
from hevc_lab.core.models import VideoInfo
from hevc_lab.errors import VideoError


def video_info(duration=20.0, fps=25.0):
    return VideoInfo(
        path=Path("input.mp4").resolve(),
        codec="hevc",
        width=1920,
        height=1080,
        fps=fps,
        duration_seconds=duration,
        video_bitrate_bps=1_000_000,
        file_size_bytes=1,
        pixel_format="yuv420p",
    )


class ReferencePreparationTests(unittest.TestCase):
    def test_hash_is_stable_and_changes_with_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "中文 sample.bin"
            path.write_bytes(b"abc")
            first = sha256_file(path)
            self.assertEqual(first, sha256_file(path))
            path.write_bytes(b"abcd")
            self.assertNotEqual(first, sha256_file(path))

    def test_window_clamps_to_available_duration(self):
        effective, frames = normalize_clip_window(video_info(duration=12), 10, 15)
        self.assertEqual(effective, 2)
        self.assertEqual(frames, 50)

    def test_window_rejects_invalid_range(self):
        with self.assertRaises(VideoError):
            normalize_clip_window(video_info(), -1, 5)
        with self.assertRaises(VideoError):
            normalize_clip_window(video_info(), 20, 5)
        with self.assertRaises(VideoError):
            normalize_clip_window(video_info(), 0, 0)

    def test_cache_key_covers_clip_settings(self):
        source = video_info()
        first = build_reference_cache_key("abc", source, 0, 15, 15)
        second = build_reference_cache_key("abc", source, 1, 15, 15)
        self.assertNotEqual(first, second)

    def test_timestamp_summary_requires_zero_based_monotonic_frames(self):
        summary = summarize_timestamps([0, 0.04, 0.08], 25)
        self.assertTrue(summary["strictly_increasing"])
        self.assertAlmostEqual(summary["max_delta_seconds"], 0.04)
        with self.assertRaises(VideoError):
            summarize_timestamps([1, 1.04], 25)
        with self.assertRaises(VideoError):
            summarize_timestamps([0, 0.04, 0.04], 25)


if __name__ == "__main__":
    unittest.main()
