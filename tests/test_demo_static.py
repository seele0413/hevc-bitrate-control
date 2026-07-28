import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = PROJECT_ROOT / "apps" / "demo"
DEMO_LIVE_ROOT = PROJECT_ROOT / "apps" / "web"
PRIVATE_RTSP_HOST = ".".join(["36", "212", "37", "229"])


class StaticDemoTests(unittest.TestCase):
    def load_data(self):
        with (DEMO_ROOT / "data" / "results.json").open(encoding="utf-8") as handle:
            return json.load(handle)

    def test_demo_has_no_backend_api_dependency(self):
        for path in ("index.html", "app.js"):
            text = (DEMO_ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("/api/jobs", text)
            self.assertNotIn("/api/runtime", text)
            self.assertNotIn("/api/streams", text)
            self.assertNotIn("UploadFile", text)

    def test_results_json_references_existing_static_assets(self):
        data = self.load_data()
        self.assertEqual(data["baseline"]["id"], "default_h264")
        self.assertEqual(data["ours"]["id"], "hevc_fixed")
        self.assertIn("CRF 36.0", data["ours"]["params"])
        self.assertIn("无roi", data["ours"]["params"])
        self.assertIn("previewSrc", data["baseline"])
        self.assertIn("previewSrc", data["ours"])
        for section in ("baseline", "ours"):
            for key in ("previewSrc", "hevcDownload"):
                href = data[section][key]
                self.assertTrue(href.startswith("videos/"))
                path = DEMO_ROOT / href
                self.assertTrue(path.exists(), f"missing asset: {href}")
        metrics = DEMO_ROOT / data["metricsDownload"]
        self.assertTrue(metrics.exists(), f"missing asset: {data['metricsDownload']}")

    def test_preview_assets_stay_below_cloudflare_single_file_limit(self):
        limit = 25 * 1024 * 1024
        for path in (DEMO_ROOT / "videos").glob("*.mp4"):
            self.assertLess(path.stat().st_size, limit, path.name)


class LiveDemoTests(unittest.TestCase):
    def test_live_demo_uses_stream_api_and_hls(self):
        index = (DEMO_LIVE_ROOT / "index.html").read_text(encoding="utf-8")
        script = (DEMO_LIVE_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("/vendor/hls.min.js", index)
        self.assertIn('id="rtspInput"', index)
        self.assertIn('id="h264Video"', index)
        self.assertIn('id="h265Video"', index)
        self.assertIn('id="divider"', index)
        self.assertIn('id="h264Bitrate"', index)
        self.assertIn('id="h264Latency"', index)
        self.assertIn('id="h265Bitrate"', index)
        self.assertIn('id="h265Latency"', index)
        self.assertIn("H.264 原生参数编码", index)
        self.assertIn("H265编码参数优化", index)
        self.assertIn("码率差", index)
        self.assertIn("/api/streams", script)
        self.assertIn("rtsp_url", script)
        self.assertNotIn("conservative_rtsp_url", script)
        self.assertIn("h264_native_playlist_url", script)
        self.assertIn("h265_optimized_playlist_url", script)
        self.assertIn("native_bitrate_mbps", script)
        self.assertIn("bandwidth_saving_pct", script)
        self.assertIn("/heartbeat", script)
        self.assertIn("sendBeacon", script)
        self.assertIn('preview_codec !== "h264"', script)
        self.assertIn("recoverMediaError", script)
        self.assertIn("startLoad", script)
        self.assertTrue((DEMO_LIVE_ROOT / "vendor" / "hls.min.js").exists())

    def test_live_demo_does_not_commit_private_rtsp_address(self):
        for path in DEMO_LIVE_ROOT.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".html", ".css", ".js", ".json", ".md"}:
                self.assertNotIn(
                    PRIVATE_RTSP_HOST,
                    path.read_text(encoding="utf-8"),
                    str(path),
                )


if __name__ == "__main__":
    unittest.main()
