import json
import queue
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from hevc_lab.config import DENOISE_CONFIG, HEVC_CONFIG
from hevc_lab.tools import Toolchain
from hevc_lab.web.streams import (
    HEARTBEAT_TIMEOUT_SECONDS,
    H265_PREVIEW_HLS_SEGMENT_SECONDS,
    HLS_PLAYLIST_SEGMENTS,
    LiveStream,
    LiveStreamManager,
    PLAYBACK_RECOVERY_BUFFER_SECONDS,
    PLAYBACK_TARGET_DELAY_SECONDS,
    RollingBitrate,
    SAVING_BASIS,
    StreamNotFound,
    StreamOutput,
    _mask_rtsp_url,
    _redact_text,
)


def fake_probe(codec="h264", width=320, height=180, fps=10):
    def run(command, **kwargs):
        payload = {
            "streams": [
                {
                    "codec_name": codec,
                    "codec_long_name": codec,
                    "profile": "High",
                    "width": width,
                    "height": height,
                    "pix_fmt": "yuv420p",
                    "avg_frame_rate": f"{fps}/1",
                }
            ],
            "format": {"format_name": "rtsp"},
        }
        return CompletedProcess(command, 0, json.dumps(payload), "")

    return run


class RecordingPipe:
    def __init__(self, max_write=None):
        self.data = bytearray()
        self.max_write = max_write
        self.closed = False

    def write(self, payload):
        if self.closed:
            raise ValueError("closed")
        raw = bytes(payload)
        length = len(raw) if self.max_write is None else min(len(raw), self.max_write)
        self.data.extend(raw[:length])
        return length

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, command, **kwargs):
        self.command = [str(item) for item in command]
        self.stdin = RecordingPipe() if kwargs.get("stdin") == subprocess.PIPE else None
        self.stdout = None
        self.stderr = []
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None and timeout is not None:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.returncode


class FakeProcessFactory:
    def __init__(self):
        self.processes = []

    def __call__(self, command, **kwargs):
        process = FakeProcess(command, **kwargs)
        self.processes.append(process)
        return process


class StopAfterOneRead:
    def __init__(self, payload, stream):
        self.payload = payload
        self.stream = stream
        self.done = False

    def read(self, size):
        if not self.done:
            self.done = True
            return self.payload
        self.stream.stop_event.set()
        return b""


class StreamManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.toolchain = Toolchain(self.root / "ffmpeg", self.root / "ffprobe")
        self.factory = FakeProcessFactory()
        self.manager = LiveStreamManager(
            streams_root=self.root / "streams",
            toolchain_factory=lambda: self.toolchain,
            process_factory=self.factory,
            probe_factory=fake_probe(),
            enable_io_threads=False,
        )

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def create(self):
        status = self.manager.create_stream(
            "rtsp://user:secret@camera.example.com/private/live?token=abc#fragment"
        )
        return self.manager._streams[status["stream_id"]]

    def test_commands_keep_source_copy_and_fixed_h265_config(self):
        stream = self.create()
        commands = [process.command for process in self.factory.processes]
        source_hls = next(
            command for command in commands if str(stream.outputs["source"].playlist_path) in command
        )
        h265_preview = next(
            command
            for command in commands
            if str(stream.outputs["h265_optimized"].playlist_path) in command
        )
        h265_encoder = next(command for command in commands if "libx265" in command)
        decoder = next(
            command
            for command in commands
            if "rawvideo" in command and "libx265" not in command
        )

        self.assertEqual(source_hls[source_hls.index("-c:v") + 1], "copy")
        self.assertNotIn("libx264", source_hls)
        self.assertNotIn("hqdn3d", " ".join(source_hls))
        self.assertEqual(source_hls[source_hls.index("-hls_time") + 1], "1")
        self.assertEqual(
            source_hls[source_hls.index("-hls_list_size") + 1],
            str(HLS_PLAYLIST_SEGMENTS),
        )
        self.assertIn("libx264", h265_preview)
        self.assertEqual(
            h265_preview[h265_preview.index("-hls_list_size") + 1],
            str(HLS_PLAYLIST_SEGMENTS),
        )
        self.assertEqual(sum(command.count("libx264") for command in commands), 1)
        self.assertEqual(
            sum(DENOISE_CONFIG.ffmpeg_filter() in command for command in commands),
            1,
        )
        self.assertEqual(
            decoder[decoder.index("-vf") + 1],
            "hqdn3d=1.5:1.0:2.5:2.0",
        )
        self.assertEqual(h265_preview[h265_preview.index("-crf") + 1], "21")
        preview_gop = str(max(1, round(10 * H265_PREVIEW_HLS_SEGMENT_SECONDS)))
        self.assertEqual(h265_preview[h265_preview.index("-g") + 1], preview_gop)
        self.assertEqual(
            h265_preview[h265_preview.index("-keyint_min") + 1],
            preview_gop,
        )
        self.assertEqual(
            h265_preview[h265_preview.index("-hls_time") + 1],
            str(H265_PREVIEW_HLS_SEGMENT_SECONDS),
        )
        self.assertEqual(h265_encoder[h265_encoder.index("-preset") + 1], "fast")
        self.assertIn("-nostats", h265_encoder)
        self.assertEqual(h265_encoder[h265_encoder.index("-crf") + 1], "36.0")
        self.assertEqual(h265_encoder[h265_encoder.index("-profile:v") + 1], "main")
        self.assertEqual(h265_encoder[h265_encoder.index("-pix_fmt") + 1], "yuv420p")
        expected_params = (
            "ref=4:bframes=4:b-adapt=2:rc-lookahead=45:keyint=100:"
            "min-keyint=20:scenecut=40:cutree=1:weightp=1:aq-mode=2:"
            "aq-strength=1.0:qg-size=32:aq-motion=0"
        )
        self.assertEqual(
            h265_encoder[h265_encoder.index("-x265-params") + 1],
            expected_params,
        )
        status = stream.public_status()
        self.assertEqual(status["saving_basis"], SAVING_BASIS)
        self.assertEqual(status["denoise_config"], DENOISE_CONFIG.public_dict())
        self.assertIsNone(status["rtsp_realtime_elapsed_seconds"])
        self.assertEqual(status["playback"]["policy"], "independent_rtsp_realtime_delay")
        self.assertEqual(
            status["playback"]["reference"],
            "rtsp_wall_clock_elapsed_since_first_source_byte",
        )
        self.assertEqual(status["playback"]["target_delay_seconds"], PLAYBACK_TARGET_DELAY_SECONDS)
        self.assertEqual(
            status["playback"]["recovery_buffer_seconds"],
            PLAYBACK_RECOVERY_BUFFER_SECONDS,
        )
        self.assertEqual(
            status["playback"]["h265_preview_hls_segment_seconds"],
            H265_PREVIEW_HLS_SEGMENT_SECONDS,
        )
        self.assertEqual(status["playback"]["hls_playlist_segments"], HLS_PLAYLIST_SEGMENTS)
        self.assertEqual(status["playback"]["heartbeat_timeout_seconds"], HEARTBEAT_TIMEOUT_SECONDS)
        self.assertEqual(
            status["outputs"]["h265_optimized"]["probe"]["fixed_config"],
            HEVC_CONFIG.public_dict(10.0),
        )

    def test_h265_progress_updates_frame_speed_and_fps(self):
        stream = self.create()
        output = stream.outputs["h265_optimized"]
        self.manager._parse_h265_progress(output, "frame=125")
        self.manager._parse_h265_progress(output, "fps=9.82")
        self.manager._parse_h265_progress(output, "speed=0.98x")
        self.assertEqual(output.encoded_frames, 125)
        self.assertEqual(output.encode_fps, 9.82)
        self.assertEqual(output.encode_speed_x, 0.98)

    def test_source_bytes_are_counted_once_and_fanned_out_unchanged(self):
        stream = self.create()
        payload = (b"\x00\x00\x00\x01\x67source" * 1000)
        source_pipe = RecordingPipe(max_write=7)
        decoder_pipe = RecordingPipe(max_write=11)
        stream.source_hls_process.stdin = source_pipe
        stream.decoder_process.stdin = decoder_pipe
        stream.ingest_process.stdout = StopAfterOneRead(payload, stream)

        self.manager._relay_source_stream(stream)

        self.assertEqual(bytes(source_pipe.data), payload)
        self.assertEqual(bytes(decoder_pipe.data), payload)
        self.assertEqual(stream.outputs["source"].bitrate.snapshot()["bytes"], len(payload))
        self.assertIsNotNone(stream.rtsp_reference_monotonic)
        with patch(
            "hevc_lab.web.streams.time.monotonic",
            return_value=stream.rtsp_reference_monotonic + 4.25,
        ):
            self.assertAlmostEqual(
                stream.public_status()["rtsp_realtime_elapsed_seconds"],
                4.25,
            )

    def test_rolling_window_and_negative_saving_are_preserved(self):
        bitrate = RollingBitrate(window_seconds=30.0)
        bitrate.add(1_000_000, now=100.0)
        first = bitrate.snapshot(now=104.0)
        self.assertEqual(first["bytes"], 1_000_000)
        self.assertAlmostEqual(first["bitrate_mbps"], 2.0)
        bitrate.add(500_000, now=135.0)
        second = bitrate.snapshot(now=135.0)
        self.assertEqual(second["bytes"], 500_000)

        source = StreamOutput("source", "source", self.root / "source", {})
        h265 = StreamOutput("h265_optimized", "h265", self.root / "h265", {})
        source.bitrate.add(100, now=100.0)
        h265.bitrate.add(150, now=100.0)
        stream = LiveStream(
            "id",
            "rtsp://camera/live",
            "rtsp://***/***",
            self.root,
            {"fps": 10},
            {"source": source, "h265_optimized": h265},
        )
        with patch("hevc_lab.web.streams.time.monotonic", return_value=101.0):
            self.assertAlmostEqual(stream.bandwidth_saving_pct(), -50.0)

    def test_non_h264_probe_is_rejected(self):
        other = LiveStreamManager(
            streams_root=self.root / "other",
            toolchain_factory=lambda: self.toolchain,
            process_factory=self.factory,
            probe_factory=fake_probe(codec="hevc"),
            enable_io_threads=False,
        )
        try:
            with self.assertRaisesRegex(ValueError, "仅支持 H.264"):
                other.create_stream("rtsp://camera/live")
        finally:
            other.close()

    def test_url_redaction_removes_credentials_path_and_tokens(self):
        url = "rtsp://alice:s3cret@camera.example.com:8554/private/live?token=abc#fragment"
        masked = _mask_rtsp_url(url)
        for secret in ("alice", "s3cret", "private", "live", "token", "abc", "fragment"):
            self.assertNotIn(secret, masked)
        log = _redact_text(f"open {url} token=abc fragment", url, masked)
        for secret in ("alice", "s3cret", "/private/live", "token=abc"):
            self.assertNotIn(secret, log)

    def test_hls_path_traversal_is_rejected(self):
        stream = self.create()
        playlist = stream.outputs["source"].playlist_path
        playlist.write_text("#EXTM3U\n", encoding="ascii")
        self.assertEqual(
            self.manager.get_hls_file(stream.stream_id, "source/live.m3u8"),
            playlist.resolve(),
        )
        for path in ("../live.m3u8", "source/../secret.ts", "unknown/live.m3u8"):
            with self.subTest(path=path):
                with self.assertRaises(StreamNotFound):
                    self.manager.get_hls_file(stream.stream_id, path)

    def test_status_and_hls_requests_renew_the_page_lease(self):
        stream = self.create()
        stream.last_heartbeat_at = time.monotonic() - 10
        stale_status_lease = stream.last_heartbeat_at
        self.manager.get_status(stream.stream_id)
        self.assertGreater(stream.last_heartbeat_at, stale_status_lease)

        playlist = stream.outputs["source"].playlist_path
        playlist.write_text("#EXTM3U\n", encoding="ascii")
        stream.last_heartbeat_at = time.monotonic() - 10
        stale_hls_lease = stream.last_heartbeat_at
        self.manager.get_hls_file(stream.stream_id, "source/live.m3u8")
        self.assertGreater(stream.last_heartbeat_at, stale_hls_lease)

    def test_queue_uses_blocking_backpressure_without_drops(self):
        stream = self.create()
        frame_size = 320 * 180 * 3 // 2
        stream.frame_queue = queue.Queue(maxsize=1)
        stream.frame_queue.put(b"old")

        class FrameSource:
            def __init__(self):
                self.sent = False

            def read(self, size):
                if not self.sent:
                    self.sent = True
                    return b"x" * size
                stream.stop_event.set()
                return b""

        stream.decoder_process.stdout = FrameSource()
        thread = threading.Thread(target=self.manager._read_decoded_frames, args=(stream,))
        thread.start()
        time.sleep(0.1)
        self.assertTrue(thread.is_alive())
        self.assertEqual(stream.dropped_frames, 0)
        self.assertEqual(stream.frame_queue.get(), b"old")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(stream.frame_queue.get_nowait()), frame_size)

    def test_stop_and_child_failure_recycle_the_whole_process_group(self):
        stream = self.create()
        processes = list(self.factory.processes)
        stream.frame_queue.put(b"buffered-frame")
        stopped = self.manager.stop_stream(stream.stream_id)
        self.assertEqual(stopped["status"], "stopped")
        self.assertTrue(all(process.terminated for process in processes))
        self.assertTrue(stream.frame_queue.empty())
        self.assertFalse(stream.stream_dir.exists())

        second = self.create()
        second.decoder_process.returncode = 1
        failed = self.manager.get_status(second.stream_id)
        self.assertEqual(failed["status"], "failed")
        deadline = time.time() + 2
        while time.time() < deadline and any(
            process.poll() is None for process in self.manager._processes(second)
        ):
            time.sleep(0.01)
        self.assertTrue(all(process.poll() is not None for process in self.manager._processes(second)))


class HeartbeatTests(unittest.TestCase):
    def test_expired_heartbeat_stops_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            toolchain = Toolchain(root / "ffmpeg", root / "ffprobe")
            manager = LiveStreamManager(
                streams_root=root / "streams",
                toolchain_factory=lambda: toolchain,
                process_factory=FakeProcessFactory(),
                probe_factory=fake_probe(),
                enable_io_threads=False,
                heartbeat_timeout_seconds=0.01,
            )
            try:
                status = manager.create_stream("rtsp://camera/live")
                stream = manager._streams[status["stream_id"]]
                stream.last_heartbeat_at = time.monotonic() - 1
                deadline = time.time() + 2
                while time.time() < deadline and stream.status != "stopped":
                    time.sleep(0.02)
                self.assertEqual(stream.status, "stopped")
                self.assertIn("心跳超时", stream.error)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
