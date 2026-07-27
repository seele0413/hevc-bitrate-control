import tempfile
import threading
import time
import unittest
from subprocess import CompletedProcess
from pathlib import Path
from typing import Dict

from fastapi.testclient import TestClient

from hevc_lab.core.models import Toolchain, VideoInfo
from hevc_lab.web.app import create_app
from hevc_lab.web.jobs import JobManager
from hevc_lab.web.preview import generate_browser_preview
from hevc_lab.web.streams import LiveStreamManager, StreamNotFound
from unittest.mock import patch


def fake_toolchain(root: Path) -> Toolchain:
    return Toolchain(root / "ffmpeg", root / "ffprobe", root / "vmaf.json")


def write_fake_payload(output_dir: Path) -> Dict:
    strategies = [
        ("default_x265", "x265原生默认", None, 500_000.0, None),
        ("generic_no_roi", "通用无 ROI 方案", "general", 450_000.0, 10.0),
        ("budget_neutral_roi", "预算中性 ROI 方案", "roi", 430_000.0, 14.0),
        ("roi_denoise_experimental", "ROI + 降噪实验项", "roi_denoise", 575_050.0, -15.01),
    ]
    payload_strategies = []
    for strategy_id, title, mode, bitrate, saving in strategies:
        filename = (
            "default_x265.mp4"
            if strategy_id == "default_x265"
            else f"{strategy_id}.mp4"
        )
        path = output_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{strategy_id}\n".encode("utf-8"))
        payload_strategies.append(
            {
                "strategy_id": strategy_id,
                "title": title,
                "mode": mode,
                "status": "completed",
                "output_path": str(path),
                "width": 1920,
                "height": 1080,
                "resolution": "1920x1080",
                "average_video_packet_bitrate_bps": bitrate,
                "average_video_packet_bitrate_mbps": bitrate / 1_000_000.0,
                "saving_vs_default_pct": saving,
                "saving_vs_general_no_roi_pct": (
                    None if strategy_id == "default_x265" else (0.0 if strategy_id == "generic_no_roi" else 4.44)
                ),
                "budget_neutral_pass": (
                    True if strategy_id == "budget_neutral_roi" else None
                ),
                "roi_quality_preserved": (
                    True if strategy_id == "budget_neutral_roi" else None
                ),
                "roi_quality_improved": (
                    True if strategy_id == "budget_neutral_roi" else None
                ),
                "selected_crf": 40.0 if mode else None,
                "vmaf_mean": 84.0 if mode else None,
                "vmaf_p5": 81.0 if mode else None,
                "ssim": 0.955 if mode else None,
                "encode_speed_x": 0.5 if mode else None,
                "target_saving_min_pct": None,
                "target_saving_max_pct": None,
                "target_saving_met": None,
                "saving_target_status": "not_applicable",
                "selection_reason": (
                    "test_selection" if mode else "default_x265_reference"
                ),
            }
        )
    for report in ("final_metrics.csv", "final_summary.md", "research_manifest.json"):
        (output_dir / report).write_text("report", encoding="utf-8")
    return {
        "schema_version": 1,
        "pipeline_version": "v1.4.0",
        "study": "test",
        "input": {
            "path": str(output_dir / "input.mp4"),
            "codec": "h264",
            "width": 1920,
            "height": 1080,
            "fps": 20.0,
            "duration_seconds": 60.0,
            "video_bitrate_bps": 490_000.0,
            "file_size_bytes": 100,
            "pixel_format": "yuv420p",
        },
        "comparison_policy": {
            "winner_selection": False,
            "deployment_conclusion": False,
        },
        "strategies": payload_strategies,
    }


def fake_preview_builder(toolchain, source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"preview")
    return {"path": str(destination)}


def wait_for_status(client: TestClient, job_id: str, status: str) -> Dict:
    for _ in range(100):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] == status:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"任务未进入 {status} 状态")


class FakeProcess:
    def __init__(self, lines=None):
        self.stderr = iter(lines or [])
        self.terminated = False
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class WebApiTests(unittest.TestCase):
    def make_client(self, runner):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.manager = JobManager(
            jobs_root=root / "web_jobs",
            roi_config_path=root / "camera.json",
            toolchain_factory=lambda: fake_toolchain(root),
            runner=runner,
            preview_builder=fake_preview_builder,
        )
        return TestClient(create_app(self.manager))

    def tearDown(self):
        manager = getattr(self, "manager", None)
        if manager is not None:
            manager.close(wait=True)
        temp = getattr(self, "temp", None)
        if temp is not None:
            temp.cleanup()

    def test_upload_validation_rejects_extension_and_empty_file(self):
        def runner(**kwargs):
            return write_fake_payload(kwargs["output_dir"])

        with self.make_client(runner) as client:
            bad = client.post(
                "/api/jobs",
                files={"file": ("bad.txt", b"video", "text/plain")},
            )
            self.assertEqual(bad.status_code, 400)
            empty = client.post(
                "/api/jobs",
                files={"file": ("empty.mp4", b"", "video/mp4")},
            )
            self.assertEqual(empty.status_code, 400)

    def test_runtime_reports_v1_4_pipeline_and_strategy_ids(self):
        def runner(**kwargs):
            return write_fake_payload(kwargs["output_dir"])

        with self.make_client(runner) as client:
            runtime = client.get("/api/runtime").json()
        self.assertEqual(runtime["pipeline_version"], "v1.4.0")
        self.assertEqual(runtime["app_version"], "1.4.0")
        self.assertEqual(
            runtime["strategy_ids"],
            [
                "default_x265",
                "generic_no_roi",
                "budget_neutral_roi",
                "roi_denoise_experimental",
            ],
        )
        self.assertEqual(runtime["live_preview"]["frontend"], "apps/demo_live")
        self.assertEqual(runtime["live_preview"]["variants"], ["source", "conservative"])

    def test_job_results_preserve_negative_saving_and_download_whitelist(self):
        def runner(**kwargs):
            kwargs["progress_callback"]("encoding_default")
            return write_fake_payload(kwargs["output_dir"])

        with self.make_client(runner) as client:
            created = client.post(
                "/api/jobs",
                files={"file": ("clip.mp4", b"video", "video/mp4")},
            )
            self.assertEqual(created.status_code, 202)
            job_id = created.json()["job_id"]
            wait_for_status(client, job_id, "completed")
            results = client.get(f"/api/jobs/{job_id}/results").json()
            balanced = next(
                item
                for item in results["strategies"]
                if item["strategy_id"] == "roi_denoise_experimental"
            )
            self.assertEqual(balanced["saving_vs_default_pct"], -15.01)
            self.assertTrue(
                balanced["download_url"].endswith("roi_denoise_experimental.mp4")
            )
            download = client.get(f"/api/jobs/{job_id}/files/roi_denoise_experimental.mp4")
            self.assertEqual(download.status_code, 200)
            traversal = client.get(
                f"/api/jobs/{job_id}/files/%2E%2E%5Csecret.mp4"
            )
            self.assertEqual(traversal.status_code, 400)

    def test_executor_keeps_second_job_queued_until_first_finishes(self):
        started = threading.Event()
        release = threading.Event()
        counter = {"value": 0}
        lock = threading.Lock()

        def runner(**kwargs):
            with lock:
                counter["value"] += 1
                index = counter["value"]
            if index == 1:
                kwargs["progress_callback"]("encoding_default")
                started.set()
                self.assertTrue(release.wait(5))
            return write_fake_payload(kwargs["output_dir"])

        with self.make_client(runner) as client:
            first = client.post(
                "/api/jobs",
                files={"file": ("one.mp4", b"video1", "video/mp4")},
            ).json()
            second = client.post(
                "/api/jobs",
                files={"file": ("two.mp4", b"video2", "video/mp4")},
            ).json()
            self.assertTrue(started.wait(5))
            second_status = client.get(f"/api/jobs/{second['job_id']}").json()
            self.assertEqual(second_status["status"], "queued")
            release.set()
            wait_for_status(client, first["job_id"], "completed")
            wait_for_status(client, second["job_id"], "completed")


class LiveStreamManagerTests(unittest.TestCase):
    def test_stream_masks_password_and_builds_hls_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            processes = []

            def factory(command, **kwargs):
                calls.append([str(item) for item in command])
                process = FakeProcess(["Input from rtsp://user:secret@10.0.0.2/live\n"])
                processes.append(process)
                return process

            def probe_factory(command, **kwargs):
                url = str(command[-1])
                codec = "hevc" if "conservative" in url else "h264"
                return CompletedProcess(
                    command,
                    0,
                    stdout=(
                        '{"streams":[{"codec_name":"%s","codec_long_name":"codec",'
                        '"profile":"Main","width":1920,"height":1080,'
                        '"pix_fmt":"yuv420p","avg_frame_rate":"20/1"}],'
                        '"format":{"format_name":"rtsp"}}'
                    )
                    % codec,
                    stderr="",
                )

            manager = LiveStreamManager(
                streams_root=root / "live_streams",
                toolchain_factory=lambda: fake_toolchain(root),
                process_factory=factory,
                probe_factory=probe_factory,
            )
            status = manager.create_stream(
                "rtsp://user:secret@10.0.0.2/source",
                "rtsp://user:secret@10.0.0.2/conservative",
            )
            self.assertEqual(status["status"], "starting")
            self.assertNotIn("secret", status["masked_url"])
            self.assertIn("masked_urls", status)
            self.assertIn("source_playlist_url", status)
            self.assertIn("conservative_playlist_url", status)
            self.assertIsNone(status["source_playlist_url"])
            self.assertIsNone(status["conservative_playlist_url"])
            self.assertEqual(len(calls), 4)
            self.assertIn("-rtsp_transport", calls[0])
            self.assertIn("-c:v copy", " ".join(calls[0]))
            self.assertIn("-c:v copy", " ".join(calls[1]))
            self.assertIn("-c:v copy", " ".join(calls[2]))
            self.assertIn("libx264", calls[3])
            self.assertNotIn("-crf", calls[3])
            self.assertNotIn("-force_key_frames", calls[3])
            self.assertNotIn("-sc_threshold", calls[3])
            self.assertIn("delete_segments+omit_endlist", " ".join(calls[0]))
            stream_dir = root / "live_streams" / status["stream_id"]
            (stream_dir / "source" / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (stream_dir / "conservative" / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            running = manager.get_status(status["stream_id"])
            self.assertEqual(running["status"], "running")
            self.assertTrue(running["source_playlist_url"].endswith("/source/live.m3u8"))
            self.assertTrue(running["conservative_playlist_url"].endswith("/conservative/live.m3u8"))
            stopped = manager.stop_stream(status["stream_id"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertTrue(processes[0].terminated)
            self.assertTrue(processes[1].terminated)
            self.assertTrue(processes[2].terminated)
            self.assertTrue(processes[3].terminated)
            self.assertFalse(stream_dir.exists())

    def test_stream_probe_reports_encoded_h264_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def probe_factory(command, **kwargs):
                return CompletedProcess(
                    command,
                    0,
                    stdout=(
                        '{"streams":[{"codec_name":"h264","codec_long_name":"H.264",'
                        '"profile":"High","width":1920,"height":1080,'
                        '"pix_fmt":"yuv420p","avg_frame_rate":"20/1"}],'
                        '"format":{"format_name":"rtsp"}}'
                    ),
                    stderr="",
                )

            manager = LiveStreamManager(
                streams_root=root / "live_streams",
                toolchain_factory=lambda: fake_toolchain(root),
                process_factory=lambda *args, **kwargs: FakeProcess(),
                probe_factory=probe_factory,
            )
            status = manager.create_stream("rtsp://127.0.0.1/live", "rtsp://127.0.0.1/live-h265")
            self.assertEqual(status["probe"]["codec"], "h264")
            self.assertEqual(status["probes"]["source"]["codec"], "h264")
            self.assertEqual(status["probe"]["width"], 1920)
            self.assertEqual(status["probe"]["height"], 1080)
            self.assertEqual(status["probe"]["fps"], 20.0)
            self.assertTrue(status["probe"]["already_encoded"])

    def test_stream_status_estimates_camera_bitrate_and_saving_from_metric_hls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = LiveStreamManager(
                streams_root=root / "live_streams",
                toolchain_factory=lambda: fake_toolchain(root),
                process_factory=lambda *args, **kwargs: FakeProcess(),
            )
            status = manager.create_stream("rtsp://127.0.0.1/source", "rtsp://127.0.0.1/conservative")
            stream_dir = root / "live_streams" / status["stream_id"]
            source_preview_dir = stream_dir / "source"
            conservative_preview_dir = stream_dir / "conservative"
            source_metric_dir = stream_dir / "metrics" / "source"
            conservative_metric_dir = stream_dir / "metrics" / "conservative"
            (source_preview_dir / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (conservative_preview_dir / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (source_metric_dir / "segment_00001.ts").write_bytes(b"a" * 250_000)
            (source_metric_dir / "segment_00002.ts").write_bytes(b"b" * 250_000)
            (source_metric_dir / "live.m3u8").write_text(
                "#EXTM3U\n#EXTINF:2.0,\nsegment_00001.ts\n#EXTINF:2.0,\nsegment_00002.ts\n",
                encoding="utf-8",
            )
            (conservative_metric_dir / "segment_00001.ts").write_bytes(b"c" * 125_000)
            (conservative_metric_dir / "live.m3u8").write_text(
                "#EXTM3U\n#EXTINF:2.0,\nsegment_00001.ts\n",
                encoding="utf-8",
            )
            running = manager.get_status(status["stream_id"])
            source_metrics = running["outputs"]["source"]["metrics"]
            conservative_metrics = running["outputs"]["conservative"]["metrics"]
            self.assertAlmostEqual(source_metrics["camera_bitrate_mbps"], 1.0)
            self.assertAlmostEqual(conservative_metrics["camera_bitrate_mbps"], 0.5)
            self.assertAlmostEqual(running["bandwidth_saving_pct"], 50.0)
            self.assertEqual(source_metrics["camera_segment_count"], 2)
            self.assertEqual(source_metrics["camera_window_seconds"], 4.0)

    def test_hls_file_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = LiveStreamManager(
                streams_root=root / "live_streams",
                toolchain_factory=lambda: fake_toolchain(root),
                process_factory=lambda *args, **kwargs: FakeProcess(),
            )
            status = manager.create_stream("rtsp://127.0.0.1/live")
            stream_dir = root / "live_streams" / status["stream_id"]
            (stream_dir / "source" / "segment_00001.ts").write_bytes(b"segment")
            path = manager.get_hls_file(status["stream_id"], "source/segment_00001.ts")
            self.assertEqual(path.name, "segment_00001.ts")
            with self.assertRaises(StreamNotFound):
                manager.get_hls_file(status["stream_id"], "../secret.ts")
            with self.assertRaises(StreamNotFound):
                manager.get_hls_file(status["stream_id"], "unknown/segment_00001.ts")


class LiveStreamApiTests(unittest.TestCase):
    def make_client(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.created_processes = []

        def factory(*args, **kwargs):
            process = FakeProcess()
            self.created_processes.append(process)
            return process

        self.stream_manager = LiveStreamManager(
            streams_root=root / "live_streams",
            toolchain_factory=lambda: fake_toolchain(root),
            process_factory=factory,
        )
        self.manager = JobManager(
            jobs_root=root / "web_jobs",
            roi_config_path=root / "camera.json",
            toolchain_factory=lambda: fake_toolchain(root),
            runner=lambda **kwargs: write_fake_payload(kwargs["output_dir"]),
            preview_builder=fake_preview_builder,
        )
        return TestClient(create_app(self.manager, self.stream_manager))

    def tearDown(self):
        stream_manager = getattr(self, "stream_manager", None)
        if stream_manager is not None:
            stream_manager.close()
        manager = getattr(self, "manager", None)
        if manager is not None:
            manager.close(wait=True)
        temp = getattr(self, "temp", None)
        if temp is not None:
            temp.cleanup()

    def test_stream_api_validates_url_and_masks_secret(self):
        with self.make_client() as client:
            missing = client.post("/api/streams", json={})
            self.assertEqual(missing.status_code, 400)
            bad = client.post("/api/streams", json={"rtsp_url": "http://example.com/live"})
            self.assertEqual(bad.status_code, 400)
            created = client.post(
                "/api/streams",
                json={"rtsp_url": "rtsp://user:secret@127.0.0.1/live"},
            )
            self.assertEqual(created.status_code, 202)
            payload = created.json()
            self.assertNotIn("secret", payload["masked_url"])
            self.assertEqual(payload["status"], "starting")

    def test_stream_api_stop_is_stable_and_unknown_returns_404(self):
        with self.make_client() as client:
            missing = client.get("/api/streams/not-found")
            self.assertEqual(missing.status_code, 404)
            created = client.post(
                "/api/streams",
                json={"rtsp_url": "rtsp://127.0.0.1/live"},
            ).json()
            first = client.delete(f"/api/streams/{created['stream_id']}")
            self.assertEqual(first.status_code, 200)
            second = client.delete(f"/api/streams/{created['stream_id']}")
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["status"], "stopped")

    def test_hls_route_blocks_path_traversal(self):
        with self.make_client() as client:
            created = client.post(
                "/api/streams",
                json={"rtsp_url": "rtsp://127.0.0.1/live"},
            ).json()
            traversal = client.get(
                f"/api/streams/{created['stream_id']}/hls/%2E%2E%2Fsecret.ts"
            )
            self.assertIn(traversal.status_code, {400, 404})
            missing = client.get(f"/api/streams/{created['stream_id']}/hls/live.m3u8")
            self.assertEqual(missing.status_code, 404)
            missing_nested = client.get(f"/api/streams/{created['stream_id']}/hls/source/live.m3u8")
            self.assertEqual(missing_nested.status_code, 404)


class WebPreviewTests(unittest.TestCase):
    def test_preview_uses_libx264_and_keeps_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            preview = root / "preview.mp4"
            source.write_bytes(b"source")
            info = VideoInfo(
                path=source,
                codec="hevc",
                width=1920,
                height=1080,
                fps=20.0,
                duration_seconds=60.0,
                video_bitrate_bps=500_000.0,
                file_size_bytes=100,
                pixel_format="yuv420p",
            )
            out = VideoInfo(
                path=preview,
                codec="h264",
                width=1920,
                height=1080,
                fps=20.0,
                duration_seconds=60.0,
                video_bitrate_bps=800_000.0,
                file_size_bytes=120,
                pixel_format="yuv420p",
            )
            with patch(
                "hevc_lab.web.preview.probe_video",
                side_effect=[info, out],
            ), patch("hevc_lab.web.preview.run_process") as mocked:
                generate_browser_preview(fake_toolchain(root), source, preview)
            command = [str(item) for item in mocked.call_args.args[0]]
            self.assertIn("libx264", command)
            self.assertIn("+faststart", command)
            self.assertNotIn("shell=True", " ".join(command))


if __name__ == "__main__":
    unittest.main()
