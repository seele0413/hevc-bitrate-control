import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess

from hevc_lab.config import DENOISE_CONFIG
from hevc_lab.tools import discover_toolchain
from hevc_lab.web.streams import LiveStreamManager


def synthetic_probe(command, **kwargs):
    payload = {
        "streams": [
            {
                "codec_name": "h264",
                "codec_long_name": "H.264 / AVC",
                "profile": "High",
                "width": 320,
                "height": 180,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "10/1",
            }
        ],
        "format": {"format_name": "rtsp"},
    }
    return CompletedProcess(command, 0, json.dumps(payload), "")


def synthetic_ingest(toolchain, stream):
    return [
        toolchain.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=10",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-g",
        "10",
        "-keyint_min",
        "10",
        "-sc_threshold",
        "0",
        "-f",
        "h264",
        "-flush_packets",
        "1",
        "pipe:1",
    ]


class RealFfmpegPipelineTests(unittest.TestCase):
    def test_synthetic_h264_elementary_stream_generates_both_hls_outputs(self):
        toolchain = discover_toolchain()
        with tempfile.TemporaryDirectory() as temp:
            manager = LiveStreamManager(
                streams_root=Path(temp) / "streams",
                toolchain_factory=lambda: toolchain,
                probe_factory=synthetic_probe,
                ingest_command_factory=synthetic_ingest,
                heartbeat_timeout_seconds=60,
            )
            processes = []
            try:
                created = manager.create_stream("rtsp://synthetic.invalid/live")
                stream_id = created["stream_id"]
                stream = manager._streams[stream_id]
                processes = [process for process in manager._processes(stream) if process]
                deadline = time.time() + 45
                status = created
                while time.time() < deadline:
                    manager.heartbeat(stream_id)
                    status = manager.get_status(stream_id)
                    source_segments = list(stream.outputs["source"].preview_dir.glob("*.ts"))
                    h265_segments = list(
                        stream.outputs["h265_optimized"].preview_dir.glob("*.ts")
                    )
                    if status["status"] == "failed":
                        self.fail(status["error"] or str(status["log_tail"]))
                    if source_segments and h265_segments and status["status"] == "running":
                        break
                    time.sleep(0.25)
                else:
                    self.fail(f"实时管线未在超时前生成两路 HLS：{status}")

                self.assertGreater(status["source_elementary_bytes_rolling_30s"], 0)
                self.assertGreater(status["h265_optimized_elementary_bytes_rolling_30s"], 0)
                self.assertIsNotNone(status["bandwidth_saving_pct"])
                self.assertEqual(
                    status["saving_basis"],
                    "source_h264_elementary_stream_bytes_vs_"
                    "denoised_h265_elementary_stream_bytes_rolling_30s",
                )
                self.assertEqual(status["denoise_config"], DENOISE_CONFIG.public_dict())

                for variant in ("source", "h265_optimized"):
                    playlist = stream.outputs[variant].playlist_path
                    segment_names = [
                        line.strip()
                        for line in playlist.read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.startswith("#")
                    ]
                    self.assertTrue(segment_names)
                    segment = playlist.parent / segment_names[0]
                    self.assertTrue(segment.is_file())
                    completed = subprocess.run(
                        [
                            str(toolchain.ffmpeg),
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(segment),
                            "-f",
                            "null",
                            "-",
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
            finally:
                if "stream_id" in locals():
                    manager.stop_stream(stream_id)
                manager.close()
            self.assertTrue(all(process.poll() is not None for process in processes))


if __name__ == "__main__":
    unittest.main()
