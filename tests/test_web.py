import unittest

from fastapi.testclient import TestClient

from hevc_lab.web.app import create_app
from hevc_lab.web.streams import StreamNotFound


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

    def test_runtime_is_v2_1_realtime_only(self):
        with TestClient(self.app) as client:
            runtime = client.get("/api/runtime").json()
        self.assertEqual(runtime["app_version"], "2.1.0")
        self.assertEqual(runtime["pipeline_version"], "v2.1.0")
        self.assertEqual(runtime["commands"], ["check-env", "web"])
        self.assertEqual(runtime["live_preview"]["variants"], ["source", "h265_optimized"])
        self.assertEqual(runtime["live_preview"]["h265_config"]["crf"], 36.0)

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


if __name__ == "__main__":
    unittest.main()
