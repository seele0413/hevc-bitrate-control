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

from hevc_lab.config import DIRECT_HEVC_HLS_CONFIG, HEVC_CONFIG
from hevc_lab.tools import Toolchain
from hevc_lab.web.streams import (
    HEARTBEAT_TIMEOUT_SECONDS,
    HLS_TRANSPORT_MEASUREMENT_BASIS,
    HLS_PLAYLIST_SEGMENTS,
    H265_TARGET_DELAY_SECONDS,
    LiveStream,
    LiveStreamManager,
    PLAYBACK_POLICY,
    PLAYBACK_RECOVERY_HIGH_WATERMARK_SECONDS,
    PLAYBACK_RECOVERY_LOW_WATERMARK_SECONDS,
    RollingBitrate,
    SOURCE_TARGET_DELAY_SECONDS,
    StreamNotFound,
    StreamOutput,
    _mask_rtsp_url,
    _redact_text,
    _hls_transport_snapshot,
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
    def __init__(self, max_write=None, on_close=None):
        self.data = bytearray()
        self.max_write = max_write
        self.on_close = on_close
        self.closed = False

    def write(self, payload):
        if self.closed:
            raise ValueError("closed")
        raw = bytes(payload)
        length = len(raw) if self.max_write is None else min(len(raw), self.max_write)
        self.data.extend(raw[:length])
        return length

    def close(self):
        if self.on_close is not None:
            self.on_close()
        self.closed = True


class FakeProcess:
    def __init__(self, command, **kwargs):
        self.command = [str(item) for item in command]
        self.events = []
        self.stdin = (
            RecordingPipe(on_close=lambda: self.events.append("close_stdin"))
            if kwargs.get("stdin") == subprocess.PIPE
            else None
        )
        self.stdout = None
        self.stderr = []
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.events.append("terminate")
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
        h265_hls = next(
            command
            for command in commands
            if str(stream.outputs["h265_optimized"].playlist_path) in command
        )
        h265_encoder = next(command for command in commands if "libx265" in command)

        self.assertEqual(source_hls[source_hls.index("-c:v") + 1], "copy")
        self.assertNotIn("libx264", source_hls)
        self.assertEqual(
            source_hls[source_hls.index("-hls_list_size") + 1],
            str(HLS_PLAYLIST_SEGMENTS),
        )
        self.assertEqual(h265_hls[h265_hls.index("-c:v") + 1], "copy")
        self.assertEqual(h265_hls[h265_hls.index("-tag:v") + 1], "hvc1")
        self.assertEqual(
            h265_hls[h265_hls.index("-hls_segment_type") + 1],
            "fmp4",
        )
        self.assertEqual(
            h265_hls[h265_hls.index("-hls_fmp4_init_filename") + 1],
            "init.mp4",
        )
        self.assertTrue(
            str(h265_hls[h265_hls.index("-hls_segment_filename") + 1]).endswith(".m4s")
        )
        self.assertNotIn("libx264", h265_hls)
        self.assertEqual(
            h265_hls[h265_hls.index("-hls_list_size") + 1],
            str(HLS_PLAYLIST_SEGMENTS),
        )
        self.assertEqual(sum(command.count("libx264") for command in commands), 0)
        self.assertEqual(h265_encoder[h265_encoder.index("-preset") + 1], "fast")
        self.assertIn("-nostats", h265_encoder)
        self.assertEqual(h265_encoder[h265_encoder.index("-crf") + 1], "36.0")
        self.assertEqual(h265_encoder[h265_encoder.index("-profile:v") + 1], "main")
        self.assertEqual(h265_encoder[h265_encoder.index("-pix_fmt") + 1], "yuv420p")
        format_index = h265_encoder.index("-f", h265_encoder.index("-x265-params"))
        self.assertEqual(h265_encoder[format_index + 1], "mpegts")
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
        self.assertEqual(status["playback"]["policy"], PLAYBACK_POLICY)
        self.assertEqual(
            status["playback"]["source_target_delay_seconds"],
            SOURCE_TARGET_DELAY_SECONDS,
        )
        self.assertEqual(
            status["playback"]["h265_target_delay_seconds"],
            H265_TARGET_DELAY_SECONDS,
        )
        self.assertEqual(
            status["playback"]["recovery_low_watermark_seconds"],
            PLAYBACK_RECOVERY_LOW_WATERMARK_SECONDS,
        )
        self.assertEqual(
            status["playback"]["recovery_high_watermark_seconds"],
            PLAYBACK_RECOVERY_HIGH_WATERMARK_SECONDS,
        )
        self.assertNotIn("target_delay_seconds", status["playback"])
        self.assertNotIn("recovery_buffer_seconds", status["playback"])
        self.assertEqual(status["playback"]["hls_playlist_segments"], HLS_PLAYLIST_SEGMENTS)
        self.assertEqual(status["playback"]["hls_segment_seconds"], 10.0)
        self.assertEqual(status["playback"]["heartbeat_timeout_seconds"], HEARTBEAT_TIMEOUT_SECONDS)
        self.assertEqual(
            status["outputs"]["h265_optimized"]["probe"]["fixed_config"],
            HEVC_CONFIG.public_dict(10.0),
        )
        self.assertEqual(
            status["hls_transport_measurement_basis"],
            HLS_TRANSPORT_MEASUREMENT_BASIS,
        )
        self.assertEqual(DIRECT_HEVC_HLS_CONFIG.segment_type, "fmp4")

    def test_hls_transport_snapshot_uses_latest_closed_segments(self):
        preview = self.root / "transport"
        preview.mkdir()
        playlist = preview / "live.m3u8"
        lines = ["#EXTM3U", '#EXT-X-MAP:URI="init.mp4"', "#EXT-X-TARGETDURATION:12"]
        durations = [12.0, 10.0, 9.0, 8.0]
        sizes = [1_200_000, 1_000_000, None, 800_000]
        for index, (duration, size) in enumerate(zip(durations, sizes)):
            suffix = ".ts" if index < 2 else ".m4s"
            name = f"segment_{index:05d}{suffix}"
            lines.extend([f"#EXTINF:{duration:.3f},", name])
            if size is not None:
                (preview / name).write_bytes(b"x" * size)
        (preview / "segment_99999.ts").write_bytes(b"x" * 5_000_000)
        (preview / "init.mp4").write_bytes(b"x" * 9_000_000)
        playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

        snapshot = _hls_transport_snapshot(playlist, window_seconds=30.0)

        self.assertEqual(snapshot["bytes"], 3_000_000)
        self.assertEqual(snapshot["duration_seconds"], 30.0)
        self.assertAlmostEqual(snapshot["bitrate_mbps"], 0.8)

    def test_hls_transport_snapshot_handles_missing_or_empty_playlist(self):
        missing = _hls_transport_snapshot(self.root / "missing.m3u8")
        self.assertIsNone(missing["bitrate_mbps"])
        self.assertEqual(missing["bytes"], 0)
        playlist = self.root / "empty.m3u8"
        playlist.write_text("#EXTM3U\n#EXTINF:1.0,\nmissing.ts\n", encoding="utf-8")
        empty = _hls_transport_snapshot(playlist)
        self.assertIsNone(empty["bitrate_mbps"])
        self.assertEqual(empty["duration_seconds"], 0.0)

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
        for process in processes:
            if process.stdin is not None:
                self.assertLess(
                    process.events.index("terminate"),
                    process.events.index("close_stdin"),
                )
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
