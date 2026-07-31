import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from hevc_lab.config import DENOISE_CONFIG
from hevc_lab.tools import discover_toolchain


class RealDenoiseFilterTests(unittest.TestCase):
    def test_fixed_filter_reduces_deterministic_luma_noise(self):
        width = 64
        height = 64
        frame_count = 20
        y_size = width * height
        frame_size = y_size * 3 // 2
        random_source = random.Random(230)
        noisy = bytearray()
        for _ in range(frame_count):
            noisy.extend(
                max(0, min(255, 128 + random_source.randint(-20, 20)))
                for _ in range(y_size)
            )
            noisy.extend(bytes([128]) * (frame_size - y_size))

        toolchain = discover_toolchain()
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            source = temp_root / "noisy.yuv"
            filtered = temp_root / "filtered.yuv"
            source.write_bytes(noisy)
            completed = subprocess.run(
                [
                    str(toolchain.ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "rawvideo",
                    "-pixel_format",
                    "yuv420p",
                    "-video_size",
                    f"{width}x{height}",
                    "-framerate",
                    "10",
                    "-i",
                    str(source),
                    "-vf",
                    DENOISE_CONFIG.ffmpeg_filter(),
                    "-pix_fmt",
                    "yuv420p",
                    "-frames:v",
                    str(frame_count),
                    "-f",
                    "rawvideo",
                    str(filtered),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr.decode("utf-8", errors="replace"),
            )
            output = filtered.read_bytes()

        self.assertEqual(len(output), len(noisy))
        source_luma = []
        filtered_luma = []
        for frame_index in range(frame_count):
            offset = frame_index * frame_size
            source_luma.extend(noisy[offset : offset + y_size])
            filtered_luma.extend(output[offset : offset + y_size])
        source_energy = sum((value - 128) ** 2 for value in source_luma)
        filtered_energy = sum((value - 128) ** 2 for value in filtered_luma)
        self.assertLess(filtered_energy, source_energy)


if __name__ == "__main__":
    unittest.main()
