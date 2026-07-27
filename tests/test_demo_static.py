import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = PROJECT_ROOT / "apps" / "demo"
DEMO_LIVE_ROOT = PROJECT_ROOT / "apps" / "demo_live"
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
        self.assertEqual(data["baseline"]["id"], "default_x265")
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
        self.assertIn("./vendor/hls.min.js", index)
        self.assertIn('id="sourceRtspInput"', index)
        self.assertIn('id="conservativeRtspInput"', index)
        self.assertIn('id="sourceVideo"', index)
        self.assertIn('id="conservativeVideo"', index)
        self.assertIn('id="divider"', index)
        self.assertIn('id="sourceBitrate"', index)
        self.assertIn('id="sourceLatency"', index)
        self.assertIn('id="conservativeBitrate"', index)
        self.assertIn('id="conservativeLatency"', index)
        self.assertIn("H.264 原生 vs H.265 保守策略", index)
        self.assertIn("带宽节省", index)
        self.assertIn("/api/streams", script)
        self.assertIn("source_rtsp_url", script)
        self.assertIn("conservative_rtsp_url", script)
        self.assertIn("source_playlist_url", script)
        self.assertIn("conservative_playlist_url", script)
        self.assertIn("camera_bitrate_mbps", script)
        self.assertIn("bandwidth_saving_pct", script)
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
