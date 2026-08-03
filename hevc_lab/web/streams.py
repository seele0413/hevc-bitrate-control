import json
import math
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from ..config import BROWSER_PREVIEW_CONFIG, HEVC_CONFIG
from ..tools import Toolchain, discover_toolchain


STREAM_PIPELINE_VERSION = "v2.2.1"
LIVE_STREAM_STATUSES = ("starting", "running", "failed", "stopped")
LIVE_VARIANTS = ("source", "h265_optimized")
HLS_PLAYLIST = "live.m3u8"
HLS_SEGMENT_PATTERN = "segment_%05d.ts"
HLS_ALLOWED_SUFFIXES = {".m3u8", ".ts"}
HLS_SEGMENT_SECONDS = BROWSER_PREVIEW_CONFIG.hls_segment_seconds
HLS_PLAYLIST_SEGMENTS = 60
BITRATE_WINDOW_SECONDS = 30.0
LIVE_BUFFER_SECONDS = 5.0
LIVE_BUFFER_MAX_BYTES = 512 * 1024 * 1024
MIN_FRAME_QUEUE_SIZE = 2
HEARTBEAT_TIMEOUT_SECONDS = 45.0
SOURCE_PLAYLIST_WARNING_SECONDS = 8.0
PROCESS_TERMINATE_TIMEOUT_SECONDS = 5.0
THREAD_JOIN_TIMEOUT_SECONDS = 3.0
PLAYBACK_POLICY = "independent_fixed_delay"
SOURCE_TARGET_DELAY_SECONDS = 10.0
H265_PREVIEW_TARGET_DELAY_SECONDS = 15.0
PLAYBACK_RECOVERY_LOW_WATERMARK_SECONDS = 1.5
PLAYBACK_RECOVERY_HIGH_WATERMARK_SECONDS = 8.0
HLS_TRANSPORT_MEASUREMENT_BASIS = (
    "closed_ts_segment_bytes_latest_30s_media_duration"
)

ToolchainFactory = Callable[[], Toolchain]
ProcessFactory = Callable[..., subprocess.Popen]
ProbeFactory = Callable[..., subprocess.CompletedProcess]
IngestCommandFactory = Callable[[Toolchain, "LiveStream"], List[Any]]


class StreamNotFound(KeyError):
    pass


class StreamNotReady(RuntimeError):
    pass


class StreamLimitExceeded(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _masked_host(hostname: str) -> str:
    if not hostname:
        return "***"
    if ":" in hostname and not hostname.count("."):
        return hostname[:4] + "***"
    parts = hostname.split(".")
    if len(parts) >= 4 and all(part.isdigit() for part in parts):
        return f"{parts[0]}.***.***.{parts[-1]}"
    if len(hostname) <= 6:
        return "***"
    return f"{hostname[:3]}***{hostname[-3:]}"


def _mask_rtsp_url(url: str) -> str:
    parsed = urlsplit(url)
    host = _masked_host(parsed.hostname or "")
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    credentials = "***:***@" if parsed.username or parsed.password else ""
    return f"{parsed.scheme}://{credentials}{host}/***"


def _redact_text(text: str, url: str, masked_url: str) -> str:
    cleaned = text.replace(url, masked_url)
    cleaned = re.sub(r"rtsps?://[^\s'\"]+", masked_url, cleaned, flags=re.IGNORECASE)
    parsed = urlsplit(url)
    sensitive = [
        unquote(parsed.username or ""),
        unquote(parsed.password or ""),
        parsed.path,
        parsed.query,
        parsed.fragment,
    ]
    for value in sensitive:
        if value and value != "/":
            cleaned = cleaned.replace(value, "***")
    return cleaned


def _validate_rtsp_url(url: str) -> str:
    cleaned = url.strip()
    parsed = urlsplit(cleaned)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("RTSP 地址端口无效") from exc
    if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
        raise ValueError("请输入有效的 rtsp:// 或 rtsps:// 地址")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("RTSP 地址端口无效")
    return cleaned


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _fps_from_fraction(value: str) -> Optional[float]:
    if not value or value == "0/0":
        return None
    if "/" not in value:
        try:
            return float(value)
        except ValueError:
            return None
    numerator, denominator = value.split("/", 1)
    try:
        den = float(denominator)
        return None if den == 0 else float(numerator) / den
    except ValueError:
        return None


def _hls_transport_snapshot(
    playlist_path: Path,
    window_seconds: float = BITRATE_WINDOW_SECONDS,
) -> Dict[str, Any]:
    result = {
        "bitrate_mbps": None,
        "bytes": 0,
        "duration_seconds": 0.0,
    }
    try:
        lines = playlist_path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return result

    closed_segments: List[Tuple[float, Path]] = []
    pending_duration: Optional[float] = None
    preview_root = playlist_path.parent.resolve()
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
            except (IndexError, ValueError):
                pending_duration = None
            continue
        if not line or line.startswith("#") or pending_duration is None:
            continue
        leaf = PurePosixPath(line)
        if (
            len(leaf.parts) != 1
            or leaf.name != line
            or Path(line).suffix.lower() != ".ts"
        ):
            pending_duration = None
            continue
        segment_path = (playlist_path.parent / line).resolve()
        if _is_inside(segment_path, preview_root):
            closed_segments.append((pending_duration, segment_path))
        pending_duration = None

    total_bytes = 0
    total_duration = 0.0
    for duration, segment_path in reversed(closed_segments):
        if total_duration >= window_seconds:
            break
        if duration <= 0:
            continue
        try:
            segment_bytes = segment_path.stat().st_size
        except OSError:
            continue
        total_bytes += segment_bytes
        total_duration += duration

    if total_duration <= 0:
        return result
    return {
        "bitrate_mbps": total_bytes * 8 / total_duration / 1_000_000,
        "bytes": total_bytes,
        "duration_seconds": total_duration,
    }


def _frame_queue_capacity(width: int, height: int, fps: float) -> int:
    frame_size = max(1, width * height * 3 // 2)
    target_frames = max(MIN_FRAME_QUEUE_SIZE, math.ceil(fps * LIVE_BUFFER_SECONDS))
    memory_limited_frames = max(
        MIN_FRAME_QUEUE_SIZE,
        LIVE_BUFFER_MAX_BYTES // frame_size,
    )
    return min(target_frames, memory_limited_frames)


@dataclass
class RollingBitrate:
    window_seconds: float = BITRATE_WINDOW_SECONDS
    samples: Deque[Tuple[float, int]] = field(default_factory=deque)
    total_bytes: int = 0
    first_sample_at: Optional[float] = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, byte_count: int, now: Optional[float] = None) -> None:
        if byte_count <= 0:
            return
        timestamp = time.monotonic() if now is None else now
        with self.lock:
            if self.first_sample_at is None:
                self.first_sample_at = timestamp
            self.samples.append((timestamp, byte_count))
            self.total_bytes += byte_count
            self._prune(timestamp)

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        with self.lock:
            self._prune(timestamp)
            if not self.samples or self.first_sample_at is None:
                return {"bitrate_mbps": None, "window_seconds": 0.0, "bytes": 0}
            oldest = max(self.first_sample_at, timestamp - self.window_seconds)
            duration = max(0.001, timestamp - oldest)
            return {
                "bitrate_mbps": self.total_bytes * 8 / duration / 1_000_000,
                "window_seconds": min(duration, self.window_seconds),
                "bytes": self.total_bytes,
            }

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff:
            _, byte_count = self.samples.popleft()
            self.total_bytes -= byte_count


@dataclass
class StreamOutput:
    variant: str
    title: str
    preview_dir: Path
    probe: Dict[str, Any]
    status: str = "starting"
    error: Optional[str] = None
    bitrate: RollingBitrate = field(default_factory=RollingBitrate, repr=False)
    encoded_frames: int = 0
    encode_fps: Optional[float] = None
    encode_speed_x: Optional[float] = None
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None

    @property
    def playlist_path(self) -> Path:
        return self.preview_dir / HLS_PLAYLIST

    @property
    def segment_pattern(self) -> Path:
        return self.preview_dir / HLS_SEGMENT_PATTERN


@dataclass
class LiveStream:
    stream_id: str
    source_url: str
    masked_url: str
    stream_dir: Path
    source_probe: Dict[str, Any]
    outputs: Dict[str, StreamOutput]
    status: str = "starting"
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    started_monotonic: float = field(default_factory=time.monotonic, repr=False)
    ingest_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    source_hls_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    decoder_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    h265_encoder_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    h265_preview_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    frame_queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    frame_queue_capacity: int = MIN_FRAME_QUEUE_SIZE
    threads: List[threading.Thread] = field(default_factory=list, repr=False)
    logs: Dict[str, List[str]] = field(default_factory=dict, repr=False)
    decoded_frames: int = 0
    delivered_frames: int = 0
    dropped_frames: int = 0
    last_heartbeat_at: float = field(default_factory=time.monotonic, repr=False)
    cleanup_started: bool = field(default=False, repr=False)
    cleanup_finished: threading.Event = field(default_factory=threading.Event, repr=False)

    def playlist_url(self, variant: str) -> Optional[str]:
        output = self.outputs.get(variant)
        if not output or output.status != "running":
            return None
        return f"/api/streams/{self.stream_id}/hls/{variant}/{HLS_PLAYLIST}"

    def output_metrics(self, output: StreamOutput) -> Dict[str, Any]:
        native = output.bitrate.snapshot()
        transport = _hls_transport_snapshot(output.playlist_path)
        result = {
            "elementary_bitrate_mbps": native["bitrate_mbps"],
            "elementary_window_seconds": native["window_seconds"],
            "elementary_bytes_in_window": native["bytes"],
            "hls_transport_bitrate_mbps": transport["bitrate_mbps"],
            "hls_transport_bytes": transport["bytes"],
            "hls_transport_duration_seconds": transport["duration_seconds"],
        }
        if output.variant == "h265_optimized":
            fps = self.source_probe.get("fps")
            backlog = None
            if isinstance(fps, (int, float)) and fps > 0:
                backlog = max(0.0, (self.decoded_frames - output.encoded_frames) / fps)
            result.update(
                {
                    "encoded_frame_count": output.encoded_frames,
                    "encode_fps": output.encode_fps,
                    "encode_speed_x": output.encode_speed_x,
                    "encoder_backlog_seconds": backlog,
                }
            )
        return result

    def bandwidth_saving_pct(self) -> Optional[float]:
        source = self.outputs["source"].bitrate.snapshot()["bitrate_mbps"]
        h265 = self.outputs["h265_optimized"].bitrate.snapshot()["bitrate_mbps"]
        if not isinstance(source, (int, float)) or source <= 0:
            return None
        if not isinstance(h265, (int, float)):
            return None
        return ((source - h265) / source) * 100

    def _current_warnings(self) -> List[str]:
        warnings = list(self.warnings)
        source = self.outputs["source"]
        if (
            self.status == "starting"
            and not source.playlist_path.is_file()
            and time.monotonic() - self.started_monotonic >= SOURCE_PLAYLIST_WARNING_SECONDS
        ):
            warnings.append("源流尚未产生 HLS 分片；摄像头关键帧间隔可能过长")
        return warnings

    def public_status(self) -> Dict[str, Any]:
        fps = self.source_probe.get("fps")
        queue_depth = self.frame_queue.qsize()
        queue_seconds = queue_depth / fps if isinstance(fps, (int, float)) and fps > 0 else None
        capacity_seconds = (
            self.frame_queue_capacity / fps
            if isinstance(fps, (int, float)) and fps > 0
            else None
        )
        output_metrics = {
            variant: self.output_metrics(output)
            for variant, output in self.outputs.items()
        }
        source_metrics = output_metrics["source"]
        h265_metrics = output_metrics["h265_optimized"]
        return {
            "stream_id": self.stream_id,
            "status": self.status,
            "masked_url": self.masked_url,
            "source_playlist_url": self.playlist_url("source"),
            "h265_optimized_playlist_url": self.playlist_url("h265_optimized"),
            "probes": {
                variant: dict(output.probe)
                for variant, output in self.outputs.items()
            },
            "bandwidth_saving_pct": self.bandwidth_saving_pct(),
            "saving_basis": (
                "source_h264_elementary_stream_bytes_vs_"
                "h265_elementary_stream_bytes_rolling_30s"
            ),
            "hls_transport_measurement_basis": HLS_TRANSPORT_MEASUREMENT_BASIS,
            "source_elementary_bitrate_mbps": source_metrics[
                "elementary_bitrate_mbps"
            ],
            "h265_optimized_elementary_bitrate_mbps": h265_metrics[
                "elementary_bitrate_mbps"
            ],
            "source_elementary_bytes_rolling_30s": source_metrics[
                "elementary_bytes_in_window"
            ],
            "h265_optimized_elementary_bytes_rolling_30s": h265_metrics[
                "elementary_bytes_in_window"
            ],
            "h265_encode_speed_x": h265_metrics["encode_speed_x"],
            "h265_encoder_backlog_seconds": h265_metrics[
                "encoder_backlog_seconds"
            ],
            "dropped_frames": self.dropped_frames,
            "frame_buffer": {
                "policy": "continuity_first_block_when_full",
                "depth_frames": queue_depth,
                "capacity_frames": self.frame_queue_capacity,
                "buffered_seconds": queue_seconds,
                "capacity_seconds": capacity_seconds,
            },
            "playback": {
                "policy": PLAYBACK_POLICY,
                "source_target_delay_seconds": SOURCE_TARGET_DELAY_SECONDS,
                "h265_preview_target_delay_seconds": H265_PREVIEW_TARGET_DELAY_SECONDS,
                "recovery_low_watermark_seconds": (
                    PLAYBACK_RECOVERY_LOW_WATERMARK_SECONDS
                ),
                "recovery_high_watermark_seconds": (
                    PLAYBACK_RECOVERY_HIGH_WATERMARK_SECONDS
                ),
                "hls_segment_seconds": HLS_SEGMENT_SECONDS,
                "hls_playlist_segments": HLS_PLAYLIST_SEGMENTS,
                "hls_retention_seconds": HLS_SEGMENT_SECONDS * HLS_PLAYLIST_SEGMENTS,
                "heartbeat_timeout_seconds": HEARTBEAT_TIMEOUT_SECONDS,
            },
            "warnings": self._current_warnings(),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "outputs": {
                variant: {
                    "variant": output.variant,
                    "title": output.title,
                    "status": output.status,
                    "playlist_url": self.playlist_url(variant),
                    "preview_mode": (
                        "source_h264_stream_copy_hls"
                        if variant == "source"
                        else "h265_native_to_h264_view_only_hls"
                    ),
                    "preview_only": variant == "h265_optimized",
                    "probe": dict(output.probe),
                    "metrics": output_metrics[variant],
                    "error": output.error,
                }
                for variant, output in self.outputs.items()
            },
            "log_tail": self.combined_log_tail(),
        }

    def combined_log_tail(self) -> List[str]:
        combined = []
        for role, lines in self.logs.items():
            combined.extend(f"[{role}] {line}" for line in lines[-3:])
        return combined[-12:]


class LiveStreamManager:
    def __init__(
        self,
        streams_root: Path,
        toolchain_factory: ToolchainFactory = discover_toolchain,
        process_factory: ProcessFactory = subprocess.Popen,
        probe_factory: ProbeFactory = subprocess.run,
        ingest_command_factory: Optional[IngestCommandFactory] = None,
        max_active_streams: int = 1,
        enable_io_threads: bool = True,
        heartbeat_timeout_seconds: float = HEARTBEAT_TIMEOUT_SECONDS,
    ) -> None:
        self.streams_root = streams_root.expanduser().resolve()
        self.toolchain_factory = toolchain_factory
        self.process_factory = process_factory
        self.probe_factory = probe_factory
        self.ingest_command_factory = ingest_command_factory
        self.max_active_streams = max_active_streams
        self.enable_io_threads = enable_io_threads
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.streams_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._streams: Dict[str, LiveStream] = {}
        self._closed = threading.Event()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            stream_ids = list(self._streams)
        for stream_id in stream_ids:
            try:
                self.stop_stream(stream_id)
            except StreamNotFound:
                pass
        if threading.current_thread() is not self._watchdog:
            self._watchdog.join(timeout=2)

    def create_stream(self, rtsp_url: str) -> Dict[str, Any]:
        source_url = _validate_rtsp_url(rtsp_url)
        with self._lock:
            active_count = sum(
                1
                for stream in self._streams.values()
                if stream.status in {"starting", "running"}
            )
            if active_count >= self.max_active_streams:
                raise StreamLimitExceeded("已有实时预览正在运行，请先停止当前拉流")

        toolchain = self.toolchain_factory()
        masked_url = _mask_rtsp_url(source_url)
        source_probe = self._probe_stream(toolchain, source_url, masked_url)
        if not source_probe.get("ok"):
            raise ValueError(f"RTSP 探测失败：{source_probe.get('error') or '未知原因'}")
        if source_probe.get("codec") != "h264":
            actual = source_probe.get("codec") or "未知"
            raise ValueError(f"仅支持 H.264 RTSP 源流，当前视频编码为 {actual}")
        width = source_probe.get("width")
        height = source_probe.get("height")
        fps = source_probe.get("fps")
        if not width or not height or not fps:
            raise ValueError("RTSP 探测失败：无法获得分辨率和帧率")

        stream_id = uuid.uuid4().hex
        stream_dir = self.streams_root / stream_id
        outputs = {
            "source": StreamOutput(
                variant="source",
                title="原始 H.264 源码流（直通）",
                preview_dir=stream_dir / "source",
                probe={
                    **source_probe,
                    "encoder": "copy",
                    "crf": None,
                    "crf_label": "源码原参数",
                },
            ),
            "h265_optimized": StreamOutput(
                variant="h265_optimized",
                title="H.265 固定参数编码",
                preview_dir=stream_dir / "h265_optimized",
                probe={
                    **source_probe,
                    "codec": "hevc",
                    "codec_long_name": "H.265 / HEVC",
                    "encoder": "libx265",
                    "crf": HEVC_CONFIG.crf,
                    "crf_label": f"{HEVC_CONFIG.crf:.1f}",
                    "preset": HEVC_CONFIG.preset,
                    "fixed_config": HEVC_CONFIG.public_dict(float(fps)),
                },
            ),
        }
        for output in outputs.values():
            output.preview_dir.mkdir(parents=True, exist_ok=True)
        capacity = _frame_queue_capacity(int(width), int(height), float(fps))
        stream = LiveStream(
            stream_id=stream_id,
            source_url=source_url,
            masked_url=masked_url,
            stream_dir=stream_dir,
            source_probe=source_probe,
            outputs=outputs,
            frame_queue=queue.Queue(maxsize=capacity),
            frame_queue_capacity=capacity,
        )
        with self._lock:
            self._streams[stream_id] = stream
        self._start_pipeline(toolchain, stream)
        return self.get_status(stream_id)

    def get_status(self, stream_id: str) -> Dict[str, Any]:
        with self._lock:
            stream = self._stream_locked(stream_id)
            if stream.status in {"starting", "running"}:
                stream.last_heartbeat_at = time.monotonic()
            self._refresh_status_locked(stream)
            return stream.public_status()

    def heartbeat(self, stream_id: str) -> Dict[str, Any]:
        with self._lock:
            stream = self._stream_locked(stream_id)
            stream.last_heartbeat_at = time.monotonic()
            self._refresh_status_locked(stream)
            return stream.public_status()

    def stop_stream(self, stream_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            stream = self._stream_locked(stream_id)
            if stream.status == "stopped":
                return stream.public_status()
            stream.status = "stopped"
            stream.error = reason
            stream.stopped_at = _now()
            stream.updated_at = stream.stopped_at
            stream.stop_event.set()
            for output in stream.outputs.values():
                output.status = "stopped"
                output.stopped_at = stream.stopped_at
        self._terminate_pipeline(stream)
        self._cleanup_stream_dir(stream)
        with self._lock:
            return stream.public_status()

    def get_hls_file(self, stream_id: str, filename: str) -> Path:
        relative = PurePosixPath(filename.replace("\\", "/"))
        parts = relative.parts
        if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
            raise StreamNotFound(filename)
        variant, leaf = parts
        if variant not in LIVE_VARIANTS or PurePosixPath(leaf).name != leaf:
            raise StreamNotFound(filename)
        if Path(leaf).suffix.lower() not in HLS_ALLOWED_SUFFIXES:
            raise StreamNotFound(filename)
        with self._lock:
            stream = self._stream_locked(stream_id)
            if stream.status in {"starting", "running"}:
                stream.last_heartbeat_at = time.monotonic()
            self._refresh_status_locked(stream)
            output = stream.outputs[variant]
            path = (output.preview_dir / leaf).resolve()
            if not _is_inside(path, output.preview_dir) or not path.is_file():
                raise StreamNotReady(filename)
            return path

    def record_elementary_bytes(
        self,
        stream_id: str,
        variant: str,
        byte_count: int,
        now: Optional[float] = None,
    ) -> None:
        with self._lock:
            output = self._stream_locked(stream_id).outputs.get(variant)
            if output is None:
                raise StreamNotFound(variant)
        output.bitrate.add(byte_count, now=now)

    def _start_pipeline(self, toolchain: Toolchain, stream: LiveStream) -> None:
        try:
            stream.source_hls_process = self._spawn_sink(
                self._source_hls_command(toolchain, stream)
            )
            stream.decoder_process = self._spawn_decoder(
                self._decoder_command(toolchain, stream)
            )
            stream.h265_preview_process = self._spawn_sink(
                self._h265_preview_command(toolchain, stream)
            )
            stream.h265_encoder_process = self._spawn_encoder(
                self._h265_encoder_command(toolchain, stream)
            )
            ingest_command = (
                self.ingest_command_factory(toolchain, stream)
                if self.ingest_command_factory
                else self._ingest_command(toolchain, stream)
            )
            stream.ingest_process = self._spawn_ingest(ingest_command)
        except Exception as exc:
            self._mark_failed(stream, str(exc))
            return

        timestamp = _now()
        stream.started_at = timestamp
        stream.started_monotonic = time.monotonic()
        stream.updated_at = timestamp
        for output in stream.outputs.values():
            output.started_at = timestamp
        if not self.enable_io_threads:
            return

        self._start_thread(stream, self._relay_source_stream, stream)
        self._start_thread(stream, self._read_decoded_frames, stream)
        self._start_thread(stream, self._feed_h265_encoder, stream)
        self._start_thread(stream, self._relay_h265_stream, stream)
        for role in (
            "ingest",
            "source_hls",
            "decoder",
            "h265_encoder",
            "h265_preview",
        ):
            self._start_thread(stream, self._consume_process_log, stream, role)
            self._start_thread(stream, self._monitor_process, stream, role)

    def _spawn_ingest(self, command: List[Any]) -> subprocess.Popen:
        return self.process_factory(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def _spawn_decoder(self, command: List[Any]) -> subprocess.Popen:
        return self.process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def _spawn_encoder(self, command: List[Any]) -> subprocess.Popen:
        return self.process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def _spawn_sink(self, command: List[Any]) -> subprocess.Popen:
        return self.process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def _start_thread(self, stream: LiveStream, target: Callable, *args: Any) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        stream.threads.append(thread)
        thread.start()

    def _relay_source_stream(self, stream: LiveStream) -> None:
        stdout = getattr(stream.ingest_process, "stdout", None)
        source_stdin = getattr(stream.source_hls_process, "stdin", None)
        decoder_stdin = getattr(stream.decoder_process, "stdin", None)
        if stdout is None or source_stdin is None or decoder_stdin is None:
            self._mark_failed(stream, "源码流管道不可用")
            return
        while not stream.stop_event.is_set():
            chunk = stdout.read(64 * 1024)
            if not chunk:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, "RTSP 源码流已中断")
                return
            stream.outputs["source"].bitrate.add(len(chunk))
            try:
                self._write_all(source_stdin, chunk)
                self._write_all(decoder_stdin, chunk)
            except (BrokenPipeError, OSError, ValueError) as exc:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, f"源码流分发失败：{exc}")
                return

    def _read_decoded_frames(self, stream: LiveStream) -> None:
        stdout = getattr(stream.decoder_process, "stdout", None)
        width = int(stream.source_probe["width"])
        height = int(stream.source_probe["height"])
        frame_size = width * height * 3 // 2
        if stdout is None:
            self._mark_failed(stream, "H.264 解码输出管道不可用")
            return
        while not stream.stop_event.is_set():
            frame = self._read_exact(stdout, frame_size)
            if len(frame) != frame_size:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, "H.264 解码输出已中断")
                return
            with self._lock:
                stream.decoded_frames += 1
            while not stream.stop_event.is_set():
                try:
                    stream.frame_queue.put(frame, timeout=0.5)
                    break
                except queue.Full:
                    continue

    def _feed_h265_encoder(self, stream: LiveStream) -> None:
        stdin = getattr(stream.h265_encoder_process, "stdin", None)
        if stdin is None:
            self._mark_failed(stream, "H.265 编码输入管道不可用")
            return
        while not stream.stop_event.is_set():
            try:
                frame = stream.frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._write_all(stdin, frame)
                with self._lock:
                    stream.delivered_frames += 1
            except (BrokenPipeError, OSError, ValueError) as exc:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, f"H.265 编码帧输入失败：{exc}")
                return

    def _relay_h265_stream(self, stream: LiveStream) -> None:
        stdout = getattr(stream.h265_encoder_process, "stdout", None)
        preview_stdin = getattr(stream.h265_preview_process, "stdin", None)
        if stdout is None or preview_stdin is None:
            self._mark_failed(stream, "H.265 码流管道不可用")
            return
        while not stream.stop_event.is_set():
            chunk = stdout.read(64 * 1024)
            if not chunk:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, "H.265 编码输出已中断")
                return
            stream.outputs["h265_optimized"].bitrate.add(len(chunk))
            try:
                self._write_all(preview_stdin, chunk)
            except (BrokenPipeError, OSError, ValueError) as exc:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, f"H.265 等价预览输入失败：{exc}")
                return

    def _consume_process_log(self, stream: LiveStream, role: str) -> None:
        process = self._process_for_role(stream, role)
        stderr = getattr(process, "stderr", None)
        if stderr is None:
            return
        for raw_line in stderr:
            if stream.stop_event.is_set():
                return
            line = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, bytes)
                else raw_line
            )
            cleaned = _redact_text(line.strip(), stream.source_url, stream.masked_url)
            if not cleaned:
                continue
            with self._lock:
                target = stream.logs.setdefault(role, [])
                target.append(cleaned)
                del target[:-20]
                if role == "h265_encoder":
                    self._parse_h265_progress(stream.outputs["h265_optimized"], cleaned)

    def _monitor_process(self, stream: LiveStream, role: str) -> None:
        process = self._process_for_role(stream, role)
        if process is None:
            return
        try:
            returncode = process.wait()
        except Exception as exc:
            if not stream.stop_event.is_set():
                self._mark_failed(stream, f"{role} 子进程监视失败：{exc}")
            return
        if not stream.stop_event.is_set():
            self._mark_failed(stream, f"{role} 子进程已退出（退出码 {returncode}）")

    def _process_for_role(self, stream: LiveStream, role: str) -> Optional[subprocess.Popen]:
        return {
            "ingest": stream.ingest_process,
            "source_hls": stream.source_hls_process,
            "decoder": stream.decoder_process,
            "h265_encoder": stream.h265_encoder_process,
            "h265_preview": stream.h265_preview_process,
        }[role]

    def _parse_h265_progress(self, output: StreamOutput, line: str) -> None:
        try:
            frame_matches = re.findall(r"(?:^|\r)frame=\s*(\d+)", line)
            fps_matches = re.findall(r"(?:^|\s)fps=\s*([0-9.]+)", line)
            speed_matches = re.findall(r"(?:^|\s)speed=\s*([0-9.]+)x", line)
            if frame_matches:
                output.encoded_frames = int(frame_matches[-1])
            if fps_matches:
                output.encode_fps = float(fps_matches[-1])
            if speed_matches:
                output.encode_speed_x = float(speed_matches[-1])
        except ValueError:
            return

    def _read_exact(self, stream: Any, size: int) -> bytes:
        chunks: List[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = stream.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _write_all(self, stream: Any, payload: bytes) -> None:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = stream.write(view[offset:])
            if written is None:
                written = len(view) - offset
            if written <= 0:
                raise BrokenPipeError("管道未接受数据")
            offset += written

    def _mark_failed(self, stream: LiveStream, message: str) -> None:
        with self._lock:
            if stream.status in {"failed", "stopped"}:
                return
            stream.status = "failed"
            stream.error = _redact_text(message, stream.source_url, stream.masked_url)
            stream.stopped_at = _now()
            stream.updated_at = stream.stopped_at
            stream.stop_event.set()
            for output in stream.outputs.values():
                output.status = "failed"
                output.error = stream.error
        threading.Thread(
            target=self._terminate_pipeline,
            args=(stream,),
            daemon=True,
        ).start()

    def _refresh_status_locked(self, stream: LiveStream) -> None:
        if stream.status in {"failed", "stopped"}:
            return
        for process in self._processes(stream):
            if process is not None and process.poll() is not None:
                self._mark_failed(stream, "实时处理子进程意外退出")
                return
        running = 0
        for output in stream.outputs.values():
            if output.playlist_path.is_file():
                output.status = "running"
                output.error = None
                running += 1
            else:
                output.status = "starting"
        stream.status = "running" if running == len(stream.outputs) else "starting"
        stream.error = None
        stream.updated_at = _now()

    def _processes(self, stream: LiveStream) -> List[Optional[subprocess.Popen]]:
        return [
            stream.ingest_process,
            stream.source_hls_process,
            stream.decoder_process,
            stream.h265_encoder_process,
            stream.h265_preview_process,
        ]

    def _terminate_pipeline(self, stream: LiveStream) -> None:
        with self._lock:
            if stream.cleanup_started:
                cleanup_started = True
            else:
                cleanup_started = False
                stream.cleanup_started = True
        if cleanup_started:
            stream.cleanup_finished.wait(timeout=15)
            return
        try:
            stream.stop_event.set()
            processes = [process for process in self._processes(stream) if process is not None]
            running = []
            for process in processes:
                if process.poll() is None:
                    try:
                        process.terminate()
                        running.append(process)
                    except (OSError, ValueError):
                        try:
                            process.kill()
                        except (OSError, ValueError):
                            pass

            deadline = time.monotonic() + PROCESS_TERMINATE_TIMEOUT_SECONDS
            survivors = []
            for process in running:
                remaining = max(0.05, deadline - time.monotonic())
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    survivors.append(process)
                except (OSError, ValueError):
                    pass
            for process in survivors:
                if process.poll() is None:
                    try:
                        process.kill()
                    except (OSError, ValueError):
                        pass
            kill_deadline = time.monotonic() + 2.0
            for process in survivors:
                remaining = max(0.05, kill_deadline - time.monotonic())
                try:
                    process.wait(timeout=remaining)
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
            current = threading.current_thread()
            thread_deadline = time.monotonic() + THREAD_JOIN_TIMEOUT_SECONDS
            for thread in stream.threads:
                if thread is not current:
                    thread.join(timeout=max(0.05, thread_deadline - time.monotonic()))
            stream.threads.clear()
            while True:
                try:
                    stream.frame_queue.get_nowait()
                except queue.Empty:
                    break
            for process in processes:
                self._close_pipe(getattr(process, "stdin", None))
                self._close_pipe(getattr(process, "stdout", None))
                self._close_pipe(getattr(process, "stderr", None))
        finally:
            stream.cleanup_finished.set()

    def _close_pipe(self, pipe: Any) -> None:
        if pipe is None:
            return
        close = getattr(pipe, "close", None)
        if not callable(close):
            return
        try:
            close()
        except (OSError, ValueError):
            pass

    def _cleanup_stream_dir(self, stream: LiveStream) -> None:
        if stream.stream_dir.is_dir() and _is_inside(stream.stream_dir, self.streams_root):
            shutil.rmtree(stream.stream_dir, ignore_errors=True)

    def _watchdog_loop(self) -> None:
        while not self._closed.wait(1.0):
            now = time.monotonic()
            with self._lock:
                expired = [
                    stream.stream_id
                    for stream in self._streams.values()
                    if stream.status in {"starting", "running"}
                    and now - stream.last_heartbeat_at > self.heartbeat_timeout_seconds
                ]
            for stream_id in expired:
                try:
                    self.stop_stream(stream_id, "页面心跳超时，实时处理已自动停止")
                except StreamNotFound:
                    pass

    def _stream_locked(self, stream_id: str) -> LiveStream:
        stream = self._streams.get(stream_id)
        if stream is None:
            raise StreamNotFound(stream_id)
        return stream

    def _probe_stream(self, toolchain: Toolchain, url: str, masked_url: str) -> Dict[str, Any]:
        command = [
            toolchain.ffprobe,
            "-hide_banner",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            "10000000",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            (
                "stream=codec_name,codec_long_name,profile,width,height,pix_fmt,"
                "r_frame_rate,avg_frame_rate,bit_rate,field_order"
            ),
            "-show_entries",
            "format=format_name,bit_rate",
            "-of",
            "json",
            url,
        ]
        try:
            completed = self.probe_factory(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        except Exception as exc:
            return {"ok": False, "error": _redact_text(str(exc), url, masked_url)}
        stdout = completed.stdout or ""
        stderr = _redact_text((completed.stderr or "").strip(), url, masked_url)
        try:
            payload = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return {"ok": False, "error": stderr or "FFprobe 输出不是 JSON"}
        if getattr(completed, "returncode", 0) not in (0, None):
            return {"ok": False, "error": stderr or "FFprobe 探测失败"}
        streams = payload.get("streams") or []
        video = streams[0] if streams else {}
        fmt = payload.get("format") or {}
        fps = _fps_from_fraction(
            video.get("avg_frame_rate") or video.get("r_frame_rate") or ""
        )
        bit_rate = video.get("bit_rate") or fmt.get("bit_rate")
        try:
            bitrate_mbps = float(bit_rate) / 1_000_000 if bit_rate is not None else None
        except (TypeError, ValueError):
            bitrate_mbps = None
        return {
            "ok": True,
            "format": fmt.get("format_name"),
            "codec": video.get("codec_name"),
            "codec_long_name": video.get("codec_long_name"),
            "profile": video.get("profile"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": fps,
            "declared_bitrate_mbps": bitrate_mbps,
            "pixel_format": video.get("pix_fmt"),
            "field_order": video.get("field_order"),
        }

    def _ingest_command(self, toolchain: Toolchain, stream: LiveStream) -> List[Any]:
        return [
            toolchain.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "+nobuffer",
            "-flags",
            "low_delay",
            "-i",
            stream.source_url,
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_mp4toannexb",
            "-f",
            "h264",
            "-flush_packets",
            "1",
            "pipe:1",
        ]

    def _source_hls_command(self, toolchain: Toolchain, stream: LiveStream) -> List[Any]:
        output = stream.outputs["source"]
        fps = float(stream.source_probe["fps"])
        return [
            toolchain.ffmpeg,
            "-hide_banner",
            "-fflags",
            "+genpts",
            "-r",
            f"{fps:.6f}",
            "-f",
            "h264",
            "-i",
            "pipe:0",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-muxdelay",
            "0",
            "-muxpreload",
            "0",
            "-f",
            "hls",
            "-hls_time",
            str(HLS_SEGMENT_SECONDS),
            "-hls_list_size",
            str(HLS_PLAYLIST_SEGMENTS),
            "-hls_flags",
            "delete_segments+omit_endlist+independent_segments",
            "-hls_segment_filename",
            output.segment_pattern,
            output.playlist_path,
        ]

    def _decoder_command(self, toolchain: Toolchain, stream: LiveStream) -> List[Any]:
        fps = float(stream.source_probe["fps"])
        return [
            toolchain.ffmpeg,
            "-hide_banner",
            "-fflags",
            "+genpts",
            "-r",
            f"{fps:.6f}",
            "-f",
            "h264",
            "-i",
            "pipe:0",
            "-map",
            "0:v:0",
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "rawvideo",
            "pipe:1",
        ]

    def _h265_encoder_command(self, toolchain: Toolchain, stream: LiveStream) -> List[Any]:
        width = int(stream.source_probe["width"])
        height = int(stream.source_probe["height"])
        fps = float(stream.source_probe["fps"])
        return [
            toolchain.ffmpeg,
            "-hide_banner",
            "-nostats",
            "-f",
            "rawvideo",
            "-pixel_format",
            "yuv420p",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            f"{fps:.6f}",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx265",
            "-preset",
            HEVC_CONFIG.preset,
            "-crf",
            f"{HEVC_CONFIG.crf:.1f}",
            "-profile:v",
            HEVC_CONFIG.profile,
            "-pix_fmt",
            HEVC_CONFIG.pixel_format,
            "-x265-params",
            HEVC_CONFIG.x265_params(fps),
            "-f",
            "hevc",
            "-flush_packets",
            "1",
            "-progress",
            "pipe:2",
            "pipe:1",
        ]

    def _h265_preview_command(self, toolchain: Toolchain, stream: LiveStream) -> List[Any]:
        output = stream.outputs["h265_optimized"]
        fps = float(stream.source_probe["fps"])
        gop = max(1, round(fps * BROWSER_PREVIEW_CONFIG.gop_seconds))
        return [
            toolchain.ffmpeg,
            "-hide_banner",
            "-fflags",
            "+genpts",
            "-r",
            f"{fps:.6f}",
            "-f",
            "hevc",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            BROWSER_PREVIEW_CONFIG.codec,
            "-preset",
            BROWSER_PREVIEW_CONFIG.preset,
            "-crf",
            str(BROWSER_PREVIEW_CONFIG.crf),
            "-maxrate",
            BROWSER_PREVIEW_CONFIG.ffmpeg_maxrate(),
            "-bufsize",
            BROWSER_PREVIEW_CONFIG.ffmpeg_bufsize(),
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-sc_threshold",
            "0",
            "-f",
            "hls",
            "-hls_time",
            str(HLS_SEGMENT_SECONDS),
            "-hls_list_size",
            str(HLS_PLAYLIST_SEGMENTS),
            "-hls_flags",
            "delete_segments+omit_endlist+independent_segments",
            "-hls_segment_filename",
            output.segment_pattern,
            output.playlist_path,
        ]
