import unittest
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from hevc_lab.web.app import _hls_response_headers, create_app
from hevc_lab.web.streams import StreamNotFound


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StubStreamManager:
    def __init__(self):
        self.created_url = None
        self.hls_file = None

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
        if stream_id == "stream-1" and self.hls_file is not None:
            return self.hls_file
        raise StreamNotFound(filename)


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.manager = StubStreamManager()
        self.app = create_app(stream_manager=self.manager)

    def test_runtime_is_v2_2_2_hevc_direct(self):
        with TestClient(self.app) as client:
            runtime = client.get("/api/runtime").json()
        self.assertEqual(runtime["app_version"], "2.2.2")
        self.assertEqual(runtime["pipeline_version"], "v2.2.2")
        self.assertEqual(runtime["commands"], ["check-env", "web"])
        self.assertEqual(runtime["live_preview"]["variants"], ["source", "h265_optimized"])
        self.assertEqual(runtime["live_preview"]["h265_config"]["crf"], 36.0)
        self.assertEqual(
            runtime["live_preview"]["h265_delivery_mode"],
            "timestamped_mpegts_to_hevc_fmp4_hls_stream_copy",
        )
        self.assertEqual(
            runtime["live_preview"]["hls_segment_types"],
            {"source": "mpegts", "h265_optimized": "fmp4"},
        )
        self.assertTrue(runtime["live_preview"]["h265_keyframe_bound_segments"])
        self.assertEqual(
            runtime["live_preview"]["playback"],
            {
                "policy": "independent_fixed_delay",
                "source_target_delay_seconds": 10.0,
                "h265_target_delay_seconds": 15.0,
                "recovery_low_watermark_seconds": 1.5,
                "recovery_high_watermark_seconds": 8.0,
                "hls_segment_seconds": 10.0,
                "hls_playlist_segments": 60,
                "hls_retention_seconds": 600.0,
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

    def test_frontend_uses_independent_fixed_delay_and_real_buffer(self):
        script = (PROJECT_ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
        page = (PROJECT_ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("targetDelay: 10", script)
        self.assertIn("targetDelay: 15", script)
        self.assertIn("const PLAYBACK_RECOVERY_LOW_WATERMARK_SECONDS = 1.5;", script)
        self.assertIn("const PLAYBACK_RECOVERY_HIGH_WATERMARK_SECONDS = 8;", script)
        self.assertIn("const HLS_BUFFER_SECONDS = 60;", script)
        self.assertIn('const HEVC_MSE_MIME = \'video/mp4; codecs="hvc1"\';', script)
        self.assertIn("const HEVC_MSE_FALLBACK_MIMES = [", script)
        self.assertIn('video/mp4; codecs="hvc1.1.6.L120.B0"', script)
        self.assertIn("const PLAYBACK_START_GRACE_MS = 2000;", script)
        self.assertIn("const PLAYER_DETACH_SETTLE_MS = 250;", script)
        self.assertIn("const BACKLOG_TREND_WINDOW_MS = 30000;", script)
        self.assertIn("function enterPlayerRecovery(key, message, countStall = false)", script)
        self.assertIn("function resumePlayerWhenReady(key)", script)
        self.assertIn("function actualBufferedAhead(video)", script)
        self.assertIn("data?.stats || data?.frag?.stats", script)
        self.assertIn("function mediaSourceSupports(mime)", script)
        self.assertIn("function supportedHevcMseMime()", script)
        self.assertIn("function supportedHevcNativeMime(video)", script)
        self.assertIn("function hevcPlaybackCapabilities()", script)
        self.assertIn("function handleH265CodecFailure()", script)
        self.assertIn('els.stopBtn.addEventListener("click", () => stopStream());', script)
        self.assertIn('if (typeof reason === "string" && reason)', script)
        self.assertIn("state.recoveryTargetTime = fixedDelayTarget(entry);", script)
        self.assertIn("bufferedAheadAt(entry.video, recoveryTarget)", script)
        self.assertIn("bufferedAheadAt(entry.video, currentTarget)", script)
        self.assertIn('video.addEventListener("waiting"', script)
        self.assertIn("liveSyncDuration: entry.targetDelay", script)
        self.assertIn("window.setTimeout(resolve, PLAYER_DETACH_SETTLE_MS)", script)
        self.assertIn(
            'function beginSplitDrag(event) {\n  if (dragging) return;\n  dragging = true;\n  els.stage.classList.add("dragging");',
            script,
        )
        self.assertIn(
            'function endSplitDrag(event) {\n  if (!dragging) return;\n  dragging = false;\n  els.stage.classList.remove("dragging");',
            script,
        )
        self.assertNotIn("wasPlayingBeforeDrag", script)
        self.assertNotIn("if (wasPlayingBeforeDrag) playBoth(true);", script)
        self.assertNotIn("userPaused || dragging || stopping", script)
        self.assertNotIn("function controlPlayers() {\n  if (dragging) return;", script)
        self.assertNotIn("function commonPlaybackTimeline()", script)
        self.assertNotIn("function seekBoth(", script)
        self.assertNotIn("function enterPlaybackRecovery(", script)
        self.assertNotIn("pauseBoth(false)", script)
        self.assertNotIn("state.recoveryTargetTime = target;\n  if (bufferedAheadAt", script)
        self.assertNotIn("liveSyncDurationCount", script)
        self.assertNotIn("if (sourcePlaylistUrl || h265PlaylistUrl)", script)
        self.assertIn('id="startupMessage"', page)
        self.assertIn('id="encodeState"', page)
        self.assertIn('id="backlogTrend"', page)
        self.assertIn('id="sourceDownloadSpeed"', page)
        self.assertIn('id="h265BandwidthMargin"', page)
        self.assertIn("H.265 直接播放", page)
        self.assertNotIn("H.264 仅观看预览", page)
        self.assertNotIn("h265_browser_preview_config", script)
        self.assertNotIn("libx264", script)

    def test_live_playlist_is_not_cacheable_or_proxy_buffered(self):
        playlist_headers = _hls_response_headers(".m3u8")
        segment_headers = _hls_response_headers(".ts")
        fmp4_headers = _hls_response_headers(".m4s")
        self.assertIn("no-store", playlist_headers["Cache-Control"])
        self.assertEqual(playlist_headers["X-Accel-Buffering"], "no")
        self.assertEqual(segment_headers["Cache-Control"], "private, max-age=120")
        self.assertEqual(segment_headers["X-Accel-Buffering"], "no")
        self.assertEqual(fmp4_headers["Cache-Control"], "private, max-age=120")

    def test_hls_response_uses_an_immutable_byte_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            playlist = Path(temp) / "live.m3u8"
            payload = b"#EXTM3U\n#EXTINF:1.0,\nsegment_00001.ts\n"
            playlist.write_bytes(payload)
            self.manager.hls_file = playlist
            with TestClient(self.app) as client:
                playlist_response = client.get(
                    "/api/streams/stream-1/hls/source/live.m3u8",
                )
                segment = Path(temp) / "segment_00001.ts"
                segment_payload = b"transport-stream-snapshot"
                segment.write_bytes(segment_payload)
                self.manager.hls_file = segment
                segment_response = client.get(
                    "/api/streams/stream-1/hls/source/segment_00001.ts",
                )
        self.assertEqual(playlist_response.status_code, 200)
        self.assertEqual(playlist_response.content, payload)
        self.assertEqual(int(playlist_response.headers["content-length"]), len(payload))
        self.assertIn("no-store", playlist_response.headers["cache-control"])
        self.assertEqual(segment_response.status_code, 200)
        self.assertEqual(segment_response.content, segment_payload)
        self.assertEqual(
            int(segment_response.headers["content-length"]),
            len(segment_payload),
        )


if __name__ == "__main__":
    unittest.main()
