import tempfile
import threading
import time
import unittest
from io import BytesIO
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


def fake_stream_probe(command, **kwargs):
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


def write_fake_payload(output_dir: Path) -> Dict:
    strategies = [
        ("default_h264", "H.264 原生编码", "h264_native", 500_000.0, None),
        ("hevc_fixed", "H.265 固定参数方案", "hevc_fixed", 575_050.0, -15.01),
    ]
    payload_strategies = []
    for strategy_id, title, mode, bitrate, saving in strategies:
        filename = f"{strategy_id}.mp4"
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
                "saving_vs_general_no_roi_pct": None,
                "budget_neutral_pass": None,
                "roi_quality_preserved": None,
                "roi_quality_improved": None,
                "selected_crf": 36.0 if strategy_id == "hevc_fixed" else None,
                "vmaf_mean": 84.0 if strategy_id == "hevc_fixed" else None,
                "vmaf_p5": 81.0 if strategy_id == "hevc_fixed" else None,
                "ssim": 0.955 if strategy_id == "hevc_fixed" else None,
                "encode_speed_x": 0.5 if strategy_id == "hevc_fixed" else None,
                "target_saving_min_pct": None,
                "target_saving_max_pct": None,
                "target_saving_met": None,
                "saving_target_status": "not_applicable",
                "selection_reason": (
                    "v1.6_fixed_crf_no_search"
                    if strategy_id == "hevc_fixed"
                    else "h264_native_reference"
                ),
            }
        )
    for report in ("final_metrics.csv", "final_summary.md", "research_manifest.json"):
        (output_dir / report).write_text("report", encoding="utf-8")
    return {
        "schema_version": 6,
            "pipeline_version": "v1.9.0",
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
        self.stdin = BytesIO()
        self.stdout = BytesIO()
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

    def test_runtime_reports_v1_6_pipeline_and_strategy_ids(self):
        def runner(**kwargs):
            return write_fake_payload(kwargs["output_dir"])

        with self.make_client(runner) as client:
            runtime = client.get("/api/runtime").json()
        self.assertEqual(runtime["pipeline_version"], "v1.9.0")
        self.assertEqual(runtime["app_version"], "1.9.0")
        self.assertEqual(
            runtime["strategy_ids"],
            [
                "default_h264",
                "hevc_fixed",
            ],
        )
        self.assertEqual(runtime["live_preview"]["frontend"], "apps/web")
        self.assertEqual(runtime["live_preview"]["variants"], ["h264_native", "h265_optimized"])

    def test_job_results_preserve_negative_saving_and_download_whitelist(self):
        def runner(**kwargs):
            kwargs["progress_callback"]("encoding_hevc_fixed")
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
                if item["strategy_id"] == "hevc_fixed"
            )
            self.assertEqual(balanced["saving_vs_default_pct"], -15.01)
            self.assertTrue(
                balanced["download_url"].endswith("hevc_fixed.mp4")
            )
            download = client.get(f"/api/jobs/{job_id}/files/hevc_fixed.mp4")
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
                kwargs["progress_callback"]("encoding_h264_native")
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
                codec = "h264"
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
                enable_io_threads=False,
            )
            status = manager.create_stream(
                "rtsp://user:secret@10.0.0.2/source",
            )
            self.assertEqual(status["status"], "starting")
            self.assertNotIn("secret", status["masked_url"])
            self.assertIn("masked_urls", status)
            self.assertEqual(status["frame_buffer"]["policy"], "realtime_drop_oldest_when_full")
            self.assertEqual(status["frame_buffer"]["capacity_frames"], 2)
            self.assertAlmostEqual(status["frame_buffer"]["capacity_seconds"], 0.1)
            self.assertIn("h264_native_playlist_url", status)
            self.assertIn("h265_optimized_playlist_url", status)
            self.assertIsNone(status["h264_native_playlist_url"])
            self.assertIsNone(status["h265_optimized_playlist_url"])
            self.assertEqual(len(calls), 5)
            h264_preview, h265_preview, h264_encoder, h265_encoder, ingest = calls
            self.assertIn("-rtsp_transport", ingest)
            self.assertEqual(sum("rtsp://" in " ".join(call) for call in calls), 1)
            self.assertIn("libx264", h264_encoder)
            self.assertNotIn("-crf", h264_encoder)
            self.assertNotIn("-preset", h264_encoder)
            self.assertNotIn("-tune", h264_encoder)
            self.assertIn("libx265", h265_encoder)
            self.assertIn("-preset fast", " ".join(h265_encoder))
            self.assertIn("-crf 36.0", " ".join(h265_encoder))
            self.assertIn("ref=4", " ".join(h265_encoder))
            self.assertIn("bframes=4", " ".join(h265_encoder))
            self.assertIn("rc-lookahead=45", " ".join(h265_encoder))
            for preview in (h264_preview, h265_preview):
                self.assertIn("libx264", preview)
                self.assertIn("-preset ultrafast", " ".join(preview))
                self.assertIn("-crf 18", " ".join(preview))
                self.assertIn("-tune zerolatency", " ".join(preview))
                self.assertIn("-hls_list_size 10", " ".join(preview))
                self.assertIn("delete_segments+omit_endlist+independent_segments", " ".join(preview))
            stream_dir = root / "live_streams" / status["stream_id"]
            (stream_dir / "h264_native" / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (stream_dir / "h265_optimized" / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            running = manager.get_status(status["stream_id"])
            self.assertEqual(running["status"], "running")
            self.assertTrue(running["h264_native_playlist_url"].endswith("/h264_native/live.m3u8"))
            self.assertTrue(running["h265_optimized_playlist_url"].endswith("/h265_optimized/live.m3u8"))
            stopped = manager.stop_stream(status["stream_id"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertTrue(all(process.terminated for process in processes))
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
                enable_io_threads=False,
            )
            status = manager.create_stream("rtsp://127.0.0.1/live")
            self.assertEqual(status["probe"]["codec"], "h264")
            self.assertEqual(status["probes"]["h264_native"]["codec"], "h264")
            self.assertEqual(status["probes"]["h265_optimized"]["codec"], "hevc")
            self.assertEqual(status["probe"]["width"], 1920)
            self.assertEqual(status["probe"]["height"], 1080)
            self.assertEqual(status["probe"]["fps"], 20.0)
            self.assertTrue(status["probe"]["already_encoded"])

    def test_stream_status_uses_native_bytes_before_preview_for_bitrate_and_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = LiveStreamManager(
                streams_root=root / "live_streams",
                toolchain_factory=lambda: fake_toolchain(root),
                process_factory=lambda *args, **kwargs: FakeProcess(),
                probe_factory=fake_stream_probe,
                enable_io_threads=False,
            )
            status = manager.create_stream("rtsp://127.0.0.1/source")
            stream_dir = root / "live_streams" / status["stream_id"]
            h264_preview_dir = stream_dir / "h264_native"
            h265_preview_dir = stream_dir / "h265_optimized"
            (h264_preview_dir / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (h265_preview_dir / "live.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            started = time.monotonic() - 4.0
            manager.record_native_bytes(status["stream_id"], "h264_native", 500_000, now=started)
            manager.record_native_bytes(status["stream_id"], "h265_optimized", 250_000, now=started)
            running = manager.get_status(status["stream_id"])
            h264_metrics = running["outputs"]["h264_native"]["metrics"]
            h265_metrics = running["outputs"]["h265_optimized"]["metrics"]
            self.assertAlmostEqual(h264_metrics["native_bitrate_mbps"], 1.0, places=2)
            self.assertAlmostEqual(h265_metrics["native_bitrate_mbps"], 0.5, places=2)
            self.assertAlmostEqual(running["bandwidth_saving_pct"], 50.0, places=2)
            self.assertEqual(running["saving_basis"], "native_elementary_stream_bytes_rolling_30s")
            self.assertEqual(running["preview_codec"], "h264")

    def test_hls_file_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = LiveStreamManager(
                streams_root=root / "live_streams",
                toolchain_factory=lambda: fake_toolchain(root),
                process_factory=lambda *args, **kwargs: FakeProcess(),
                probe_factory=fake_stream_probe,
                enable_io_threads=False,
            )
            status = manager.create_stream("rtsp://127.0.0.1/live")
            stream_dir = root / "live_streams" / status["stream_id"]
            (stream_dir / "h264_native" / "segment_00001.ts").write_bytes(b"segment")
            path = manager.get_hls_file(status["stream_id"], "h264_native/segment_00001.ts")
            self.assertEqual(path.name, "segment_00001.ts")
            (stream_dir / "h265_optimized" / "segment_00001.ts").write_bytes(b"segment")
            h265_path = manager.get_hls_file(status["stream_id"], "h265_optimized/segment_00001.ts")
            self.assertEqual(h265_path.name, "segment_00001.ts")
            with self.assertRaises(StreamNotFound):
                manager.get_hls_file(status["stream_id"], "h265_optimized/segment_00001.m4s")
            with self.assertRaises(StreamNotFound):
                manager.get_hls_file(status["stream_id"], "../secret.ts")
            with self.assertRaises(StreamNotFound):
                manager.get_hls_file(status["stream_id"], "unknown/segment_00001.ts")

    def test_missing_heartbeat_stops_all_pipeline_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            processes = []

            def factory(*args, **kwargs):
                process = FakeProcess()
                processes.append(process)
                return process

            manager = LiveStreamManager(
                streams_root=root / "live_streams",
                toolchain_factory=lambda: fake_toolchain(root),
                process_factory=factory,
                probe_factory=fake_stream_probe,
                enable_io_threads=False,
                heartbeat_timeout_seconds=0.05,
            )
            try:
                created = manager.create_stream("rtsp://127.0.0.1/live")
                deadline = time.time() + 2.0
                status = created
                while time.time() < deadline and status["status"] != "stopped":
                    time.sleep(0.05)
                    status = manager.get_status(created["stream_id"])
                self.assertEqual(status["status"], "stopped")
                self.assertTrue(all(process.terminated for process in processes))
            finally:
                manager.close()


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
            probe_factory=fake_stream_probe,
            enable_io_threads=False,
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

    def test_stream_api_heartbeat_and_page_stop_manage_the_session(self):
        with self.make_client() as client:
            created = client.post(
                "/api/streams",
                json={"rtsp_url": "rtsp://127.0.0.1/live"},
            ).json()
            stream_id = created["stream_id"]
            heartbeat = client.post(f"/api/streams/{stream_id}/heartbeat")
            self.assertEqual(heartbeat.status_code, 200)
            stopped = client.post(f"/api/streams/{stream_id}/stop", content=b"")
            self.assertEqual(stopped.status_code, 200)
            self.assertEqual(stopped.json()["status"], "stopped")
            self.assertTrue(all(process.terminated for process in self.created_processes))

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
            missing_nested = client.get(f"/api/streams/{created['stream_id']}/hls/h264_native/live.m3u8")
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
