import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Dict

from fastapi.testclient import TestClient

from hevc_lab.core.models import Toolchain, VideoInfo
from hevc_lab.web.app import create_app
from hevc_lab.web.jobs import JobManager
from hevc_lab.web.preview import generate_browser_preview
from unittest.mock import patch


def fake_toolchain(root: Path) -> Toolchain:
    return Toolchain(root / "ffmpeg", root / "ffprobe", root / "vmaf.json")


def write_fake_payload(output_dir: Path) -> Dict:
    strategies = [
        ("default_x265", "x265原生默认", None, 500_000.0, None),
        ("composite_conservative", "保守综合策略", "conservative", 620_000.0, -24.0),
        ("composite_balanced", "均衡综合策略", "balanced", 575_050.0, -15.01),
        ("composite_aggressive", "激进综合策略", "aggressive", 260_000.0, 48.0),
    ]
    payload_strategies = []
    for strategy_id, title, mode, bitrate, saving in strategies:
        filename = (
            "default_x265.mp4"
            if strategy_id == "default_x265"
            else f"composite_{mode}.mp4"
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
            }
        )
    for report in ("final_metrics.csv", "final_summary.md", "research_manifest.json"):
        (output_dir / report).write_text("report", encoding="utf-8")
    return {
        "schema_version": 1,
        "pipeline_version": "v0.11.0",
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
                if item["strategy_id"] == "composite_balanced"
            )
            self.assertEqual(balanced["saving_vs_default_pct"], -15.01)
            self.assertTrue(balanced["download_url"].endswith("composite_balanced.mp4"))
            download = client.get(f"/api/jobs/{job_id}/files/composite_balanced.mp4")
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
