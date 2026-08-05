import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess

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


def synthetic_probe_1080p(command, **kwargs):
    payload = {
        "streams": [
            {
                "codec_name": "h264",
                "codec_long_name": "H.264 / AVC",
                "profile": "High",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "20/1",
            }
        ],
        "format": {"format_name": "rtsp"},
    }
    return CompletedProcess(command, 0, json.dumps(payload), "")


def synthetic_ingest_1080p(toolchain, stream):
    return [
        toolchain.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1920x1080:rate=20",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-b:v",
        "700k",
        "-maxrate",
        "800k",
        "-bufsize",
        "1600k",
        "-g",
        "20",
        "-keyint_min",
        "20",
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
                        stream.outputs["h265_optimized"].preview_dir.glob("*.m4s")
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
                    "h265_elementary_stream_bytes_rolling_30s",
                )

                for variant in ("source", "h265_optimized"):
                    playlist = stream.outputs[variant].playlist_path
                    segment_names = [
                        line.strip()
                        for line in playlist.read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.startswith("#")
                    ]
                    self.assertTrue(segment_names)
                    playlist_text = playlist.read_text(encoding="utf-8")
                    self.assertTrue(all(
                        float(line.split(":", 1)[1].split(",", 1)[0]) > 0
                        for line in playlist_text.splitlines()
                        if line.startswith("#EXTINF:")
                    ))
                    if variant == "h265_optimized":
                        self.assertIn('#EXT-X-MAP:URI="init.mp4"', playlist_text)
                        self.assertTrue((playlist.parent / "init.mp4").is_file())
                    completed = subprocess.run(
                        [
                            str(toolchain.ffmpeg),
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(playlist),
                            "-t",
                            "3",
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

    @unittest.skipUnless(
        os.environ.get("HBC_LONG_INTEGRATION") == "1",
        "设置 HBC_LONG_INTEGRATION=1 执行 1080p/20fps 远程稳定长测",
    )
    def test_remote_stable_1080p_pipeline_runs_for_120_seconds(self):
        toolchain = discover_toolchain()
        with tempfile.TemporaryDirectory() as temp:
            manager = LiveStreamManager(
                streams_root=Path(temp) / "streams",
                toolchain_factory=lambda: toolchain,
                probe_factory=synthetic_probe_1080p,
                ingest_command_factory=synthetic_ingest_1080p,
                heartbeat_timeout_seconds=180,
            )
            processes = []
            queue_depths = []
            try:
                created = manager.create_stream("rtsp://synthetic.invalid/remote-stable")
                stream_id = created["stream_id"]
                stream = manager._streams[stream_id]
                processes = [process for process in manager._processes(stream) if process]
                self.assertEqual(len(processes), 5)

                deadline = time.monotonic() + 150
                running_since = None
                status = created
                while time.monotonic() < deadline:
                    manager.heartbeat(stream_id)
                    status = manager.get_status(stream_id)
                    if status["status"] == "failed":
                        self.fail(status["error"] or str(status["log_tail"]))
                    playlists_ready = all(
                        stream.outputs[variant].playlist_path.is_file()
                        for variant in ("source", "h265_optimized")
                    )
                    if playlists_ready and status["status"] == "running":
                        running_since = running_since or time.monotonic()
                        queue_depths.append(status["frame_buffer"]["depth_frames"])
                        if time.monotonic() - running_since >= 120:
                            break
                    time.sleep(1)
                else:
                    self.fail(f"1080p/20fps 管线未完成 120 秒运行：{status}")

                h265_metrics = status["outputs"]["h265_optimized"]["metrics"]
                self.assertIsNotNone(h265_metrics["hls_transport_bitrate_mbps"])
                self.assertGreater(h265_metrics["hls_transport_bitrate_mbps"], 0)
                self.assertGreaterEqual(h265_metrics["hls_transport_duration_seconds"], 29.0)
                self.assertTrue(queue_depths)
                self.assertLessEqual(queue_depths[-1], status["frame_buffer"]["capacity_frames"])
                print(
                    "\nremote-stable metrics: "
                    f"source={status['outputs']['source']['metrics']['hls_transport_bitrate_mbps']:.3f} Mbps, "
                    f"h265={h265_metrics['hls_transport_bitrate_mbps']:.3f} Mbps, "
                    f"queue={queue_depths[-1]}/{status['frame_buffer']['capacity_frames']}"
                )

                for variant in ("source", "h265_optimized"):
                    playlist = stream.outputs[variant].playlist_path
                    segment_names = [
                        line.strip()
                        for line in playlist.read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.startswith("#")
                    ]
                    self.assertGreaterEqual(len(segment_names), 2)
                    playlist_text = playlist.read_text(encoding="utf-8")
                    self.assertTrue(all(
                        float(line.split(":", 1)[1].split(",", 1)[0]) > 0
                        for line in playlist_text.splitlines()
                        if line.startswith("#EXTINF:")
                    ))
                    if variant == "h265_optimized":
                        self.assertIn('#EXT-X-MAP:URI="init.mp4"', playlist_text)
                        self.assertTrue((playlist.parent / "init.mp4").is_file())
                    completed = subprocess.run(
                        [
                            str(toolchain.ffmpeg),
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(playlist),
                            "-t",
                            "3",
                            "-f",
                            "null",
                            "-",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=30,
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
