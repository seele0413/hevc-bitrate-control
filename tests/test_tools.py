import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from hevc_lab.errors import ToolError
from hevc_lab.tools import Toolchain, check_capabilities


def capability_results(include_hqdn3d=True):
    filter_lines = " TS hqdn3d Apply a High Quality 3D Denoiser.\n" if include_hqdn3d else ""
    outputs = [
        "ffmpeg version test\n",
        "ffprobe version test\n",
        " V..... libx265 test\n V..... libx264 test\n",
        " V..... h264 test\n",
        " E hls test\n",
        " D rtsp test\n",
        filter_lines,
        "Input:\nrtsp\n",
    ]
    return [CompletedProcess([], 0, output, "") for output in outputs]


class ToolCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.toolchain = Toolchain(Path("ffmpeg"), Path("ffprobe"))

    def test_hqdn3d_is_reported_as_required_capability(self):
        with patch("hevc_lab.tools.run_process", side_effect=capability_results()):
            info = check_capabilities(self.toolchain)
        self.assertTrue(info["hqdn3d_denoise"])

    def test_missing_hqdn3d_fails_environment_check(self):
        with patch(
            "hevc_lab.tools.run_process",
            side_effect=capability_results(include_hqdn3d=False),
        ):
            with self.assertRaisesRegex(ToolError, "hqdn3d"):
                check_capabilities(self.toolchain)


if __name__ == "__main__":
    unittest.main()
