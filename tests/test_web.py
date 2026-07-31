import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from hevc_lab.web.app import _hls_response_headers, create_app
from hevc_lab.web.streams import StreamNotFound


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StubStreamManager:
    def __init__(self):
        self.created_url = None

    def create_stream(self, rtsp_url):
        self.created_url = rtsp_url
        return {"stream_id": "stream-1", "status": "starting"}

    def get_status(self, stream_id):
        if stream_id != "stream-1":
            raise StreamNotFound(stream_id)
        return {"stream_id": stream_id, "status": "running"}

    def heartbeat(self, stream_id):
        return self.get_status(stream_id)

    def stop_stream(self, stream_id, reason=None):
        if stream_id != "stream-1":
            raise StreamNotFound(stream_id)
        return {"stream_id": stream_id, "status": "stopped", "error": reason}

    def get_hls_file(self, stream_id, filename):
        raise StreamNotFound(filename)


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.manager = StubStreamManager()
        self.app = create_app(stream_manager=self.manager)

    def test_runtime_is_v2_3_realtime_denoise(self):
        with TestClient(self.app) as client:
            runtime = client.get("/api/runtime").json()
        self.assertEqual(runtime["app_version"], "2.3.0")
        self.assertEqual(runtime["pipeline_version"], "v2.3.0")
        self.assertEqual(runtime["commands"], ["check-env", "web"])
        self.assertEqual(runtime["live_preview"]["variants"], ["source", "h265_optimized"])
        self.assertEqual(runtime["live_preview"]["h265_config"]["crf"], 36.0)
        self.assertEqual(
            runtime["live_preview"]["saving_basis"],
            "source_h264_elementary_stream_bytes_vs_"
            "denoised_h265_elementary_stream_bytes_rolling_30s",
        )
        self.assertEqual(
            runtime["live_preview"]["denoise_config"],
            {
                "enabled": True,
                "filter": "hqdn3d",
                "profile": "light_detail_preserving",
                "luma_spatial": 1.5,
                "chroma_spatial": 1.0,
                "luma_temporal": 2.5,
                "chroma_temporal": 2.0,
                "placement": "after_h264_decode_before_h265_frame_queue",
            },
        )
        self.assertEqual(
            runtime["live_preview"]["playback"],
            {
                "policy": "independent_rtsp_realtime_delay",
                "reference": "rtsp_wall_clock_elapsed_since_first_source_byte",
                "target_delay_seconds": 0.0,
                "recovery_buffer_seconds": 1.0,
                "hls_segment_seconds": 1,
                "h265_preview_hls_segment_seconds": 0.5,
                "hls_playlist_segments": 60,
                "hls_retention_seconds": 60,
                "h265_preview_hls_retention_seconds": 30.0,
                "heartbeat_timeout_seconds": 45.0,
            },
        )

    def test_stream_create_accepts_only_rtsp_url(self):
        with TestClient(self.app) as client:
            accepted = client.post("/api/streams", json={"rtsp_url": "rtsp://camera/live"})
            renamed = client.post(
                "/api/streams",
                json={"source_" + "rtsp_url": "rtsp://camera/live"},
            )
            extra = client.post(
                "/api/streams",
                json={"rtsp_url": "rtsp://camera/live", "extra": "no"},
            )
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(self.manager.created_url, "rtsp://camera/live")
        self.assertEqual(renamed.status_code, 400)
        self.assertEqual(extra.status_code, 400)

    def test_legacy_job_routes_are_not_registered(self):
        legacy_prefix = "/api/" + "jobs"
        self.assertFalse(
            any(getattr(route, "path", "").startswith(legacy_prefix) for route in self.app.routes)
        )

    def test_frontend_uses_independent_rtsp_delay_and_backlog_health(self):
        script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
        page = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const PLAYBACK_TARGET_DELAY_SECONDS = 0;", script)
        self.assertIn("const PLAYBACK_RECOVERY_BUFFER_SECONDS = 1;", script)
        self.assertIn("const H265_PREVIEW_HLS_SEGMENT_SECONDS = 0.5;", script)
        self.assertIn("const HLS_RETENTION_SECONDS = 60;", script)
        self.assertIn("const SYNC_GRACE_MS = 2000;", script)
        self.assertIn("const BACKLOG_TREND_WINDOW_MS = 30000;", script)
        self.assertIn("rtsp_realtime_elapsed_seconds", script)
        self.assertIn("function playerRtspLatency(video)", script)
        self.assertIn("function enterPlayerRecovery(key, message)", script)
        self.assertIn("function controlPlayers()", script)
        self.assertIn('video.addEventListener("waiting"', script)
        self.assertIn("if (PLAYBACK_TARGET_DELAY_SECONDS > 0)", script)
        self.assertIn("hlsConfig.liveSyncDuration = PLAYBACK_TARGET_DELAY_SECONDS", script)
        self.assertIn(
            "playback.h265_preview_hls_segment_seconds !== H265_PREVIEW_HLS_SEGMENT_SECONDS",
            script,
        )
        self.assertIn("目标分片", script)
        self.assertIn("安全缓冲", script)
        self.assertIn(
            "hlsConfig.liveMaxLatencyDuration = HLS_RETENTION_SECONDS - PLAYBACK_RECOVERY_BUFFER_SECONDS",
            script,
        )
        self.assertNotIn("liveSyncDuration: PLAYBACK_TARGET_DELAY_SECONDS", script)
        self.assertNotIn("liveMaxLatencyDuration: HLS_RETENTION_SECONDS", script)
        self.assertNotIn("liveSyncDurationCount", script)
        self.assertNotIn("if (sourcePlaylistUrl || h265PlaylistUrl)", script)
        self.assertNotIn("commonPlaybackTimeline", script)
        self.assertNotIn("function seekBoth", script)
        self.assertNotIn("SOFT_SYNC_THRESHOLD_SECONDS", script)
        self.assertNotIn("HARD_SYNC_THRESHOLD_SECONDS", script)
        self.assertNotIn("player.latency", script)
        self.assertNotIn("wasPlayingBeforeDrag", script)
        drag_start = script.index("function beginSplitDrag(event)")
        drag_end = script.index("function moveSplitDrag(event)")
        drag_block = script[drag_start:drag_end]
        self.assertNotIn(".pause()", drag_block)
        self.assertNotIn("playBoth", drag_block)
        self.assertIn('id="startupMessage"', page)
        self.assertIn('id="encodeState"', page)
        self.assertIn('id="backlogTrend"', page)
        self.assertIn("RTSP 滞后", page)
        self.assertIn("贴近最新安全边缘", page)
        self.assertIn("不强制同帧", page)
        self.assertIn('runtime.app_version !== "2.3.0"', script)
        self.assertIn("runtime.live_preview?.denoise_config", script)
        self.assertIn("denoise.luma_spatial", script)
        self.assertIn("function configDecimal(value)", script)
        self.assertIn("H.265 固定编码 · 轻度降噪", page)
        self.assertIn("节省结果包含降噪影响", page)

    def test_live_playlist_is_not_cacheable_or_proxy_buffered(self):
        playlist_headers = _hls_response_headers(".m3u8")
        segment_headers = _hls_response_headers(".ts")
        self.assertIn("no-store", playlist_headers["Cache-Control"])
        self.assertEqual(playlist_headers["X-Accel-Buffering"], "no")
        self.assertEqual(segment_headers["Cache-Control"], "private, max-age=120")
        self.assertEqual(segment_headers["X-Accel-Buffering"], "no")


if __name__ == "__main__":
    unittest.main()
