import json
import math
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from ..core.configs import v1_comparison_plan
from ..core.models import Toolchain
from ..tools import discover_toolchain


LIVE_STREAM_STATUSES = ("starting", "running", "failed", "stopped")
LIVE_VARIANTS = ("h264_native", "h265_optimized")
HLS_PLAYLIST = "live.m3u8"
HLS_SEGMENT_PATTERN = "segment_%05d.ts"
HLS_ALLOWED_SUFFIXES = {".m3u8", ".ts"}
HEVC_FIXED_CRF = 36.0
HEVC_FIXED_PRESET = "fast"
PREVIEW_CRF = 18
PREVIEW_PRESET = "ultrafast"
BITRATE_WINDOW_SECONDS = 30.0
LIVE_BUFFER_SECONDS = 5.0
LIVE_BUFFER_MAX_BYTES = 512 * 1024 * 1024
MIN_FRAME_QUEUE_SIZE = 2
HEARTBEAT_TIMEOUT_SECONDS = 10.0

ToolchainFactory = Callable[[], Toolchain]
ProcessFactory = Callable[..., subprocess.Popen]
ProbeFactory = Callable[..., subprocess.CompletedProcess]


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
        return ""
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
    if parsed.port is not None and host:
        host = f"{host}:{parsed.port}"
    if parsed.username or parsed.password:
        host = f"***:***@{host}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def _validate_rtsp_url(url: str) -> str:
    cleaned = url.strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.netloc:
        raise ValueError("请输入有效的 rtsp:// 或 rtsps:// 地址")
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


def _frame_queue_capacity(width: int, height: int, fps: float) -> int:
    frame_size = max(1, width * height * 3 // 2)
    target_frames = max(MIN_FRAME_QUEUE_SIZE, math.ceil(fps * LIVE_BUFFER_SECONDS))
    memory_limited_frames = max(MIN_FRAME_QUEUE_SIZE, LIVE_BUFFER_MAX_BYTES // frame_size)
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
            bitrate = self.total_bytes * 8 / duration / 1_000_000
            return {
                "bitrate_mbps": bitrate,
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
    encoder_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    preview_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    status: str = "starting"
    error: Optional[str] = None
    log_tail: List[str] = field(default_factory=list)
    preview_log_tail: List[str] = field(default_factory=list)
    bitrate: RollingBitrate = field(default_factory=RollingBitrate, repr=False)
    encoded_frames: int = 0
    encode_fps: Optional[float] = None
    encode_speed_x: Optional[float] = None
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    preview_mode: str = "decoded_native_to_h264_hls"

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
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    ingest_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    frame_queue: queue.Queue = field(default_factory=queue.Queue, repr=False)
    frame_queue_capacity: int = MIN_FRAME_QUEUE_SIZE
    threads: List[threading.Thread] = field(default_factory=list, repr=False)
    source_frames: int = 0
    delivered_frames: int = 0
    dropped_frames: int = 0
    last_heartbeat_at: float = field(default_factory=time.monotonic, repr=False)
    cleanup_started: bool = field(default=False, repr=False)

    def playlist_url(self, variant: str) -> Optional[str]:
        output = self.outputs.get(variant)
        if not output or output.status != "running":
            return None
        return f"/api/streams/{self.stream_id}/hls/{variant}/{HLS_PLAYLIST}"

    def output_metrics(self, output: StreamOutput) -> Dict[str, Any]:
        native = output.bitrate.snapshot()
        fps = self.source_probe.get("fps")
        latency = None
        if isinstance(fps, (int, float)) and fps > 0:
            latency = max(0.0, (self.delivered_frames - output.encoded_frames) / fps)
        return {
            "native_bitrate_mbps": native["bitrate_mbps"],
            "native_window_seconds": native["window_seconds"],
            "native_bytes_in_window": native["bytes"],
            "encoded_bitrate_mbps": native["bitrate_mbps"],
            "encoded_window_seconds": native["window_seconds"],
            "encoded_frame_count": output.encoded_frames,
            "encode_fps": output.encode_fps,
            "encode_speed_x": output.encode_speed_x,
            "dropped_frames": self.dropped_frames,
            "latency_seconds": latency,
        }

    def bandwidth_saving_pct(self) -> Optional[float]:
        h264 = self.outputs.get("h264_native")
        h265 = self.outputs.get("h265_optimized")
        if not h264 or not h265:
            return None
        h264_mbps = h264.bitrate.snapshot()["bitrate_mbps"]
        h265_mbps = h265.bitrate.snapshot()["bitrate_mbps"]
        if not isinstance(h264_mbps, (int, float)) or h264_mbps <= 0:
            return None
        if not isinstance(h265_mbps, (int, float)):
            return None
        return ((h264_mbps - h265_mbps) / h264_mbps) * 100

    def public_status(self) -> Dict[str, Any]:
        h264_url = self.playlist_url("h264_native")
        h265_url = self.playlist_url("h265_optimized")
        probes = {variant: dict(output.probe) for variant, output in self.outputs.items()}
        fps = self.source_probe.get("fps")
        queue_depth = self.frame_queue.qsize()
        queue_seconds = queue_depth / fps if isinstance(fps, (int, float)) and fps > 0 else None
        queue_capacity_seconds = (
            self.frame_queue_capacity / fps
            if isinstance(fps, (int, float)) and fps > 0
            else None
        )
        return {
            "stream_id": self.stream_id,
            "status": self.status,
            "masked_url": self.masked_url,
            "masked_urls": {variant: self.masked_url for variant in LIVE_VARIANTS},
            "playlist_url": h264_url,
            "h264_native_playlist_url": h264_url,
            "h265_optimized_playlist_url": h265_url,
            "probe": probes.get("h264_native", {}),
            "probes": probes,
            "bandwidth_saving_pct": self.bandwidth_saving_pct(),
            "saving_basis": "native_elementary_stream_bytes_rolling_30s",
            "preview_codec": "h264",
            "preview_only": True,
            "dropped_frames": self.dropped_frames,
            "frame_buffer": {
                "policy": "continuity_first_block_when_full",
                "depth_frames": queue_depth,
                "capacity_frames": self.frame_queue_capacity,
                "buffered_seconds": queue_seconds,
                "capacity_seconds": queue_capacity_seconds,
            },
            "error": self.error,
            "last_error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "outputs": {
                variant: {
                    "variant": output.variant,
                    "title": output.title,
                    "status": output.status,
                    "metric_status": "native_elementary_stream",
                    "playlist_url": self.playlist_url(variant),
                    "preview_url": self.playlist_url(variant),
                    "error": output.error,
                    "preview_mode": output.preview_mode,
                    "probe": dict(output.probe),
                    "metrics": self.output_metrics(output),
                    "log_tail": list(output.log_tail[-5:]),
                    "preview_log_tail": list(output.preview_log_tail[-5:]),
                }
                for variant, output in self.outputs.items()
            },
            "log_tail": self.combined_log_tail(),
        }

    def combined_log_tail(self) -> List[str]:
        lines: List[str] = []
        for variant in LIVE_VARIANTS:
            output = self.outputs.get(variant)
            if output:
                lines.extend(f"{variant}: {line}" for line in output.log_tail[-2:])
                lines.extend(f"{variant} preview: {line}" for line in output.preview_log_tail[-1:])
        return lines[-6:]


class LiveStreamManager:
    def __init__(
        self,
        streams_root: Path,
        toolchain_factory: ToolchainFactory = discover_toolchain,
        process_factory: ProcessFactory = subprocess.Popen,
        probe_factory: ProbeFactory = subprocess.run,
        max_active_streams: int = 1,
        enable_io_threads: bool = True,
        heartbeat_timeout_seconds: float = HEARTBEAT_TIMEOUT_SECONDS,
    ) -> None:
        self.streams_root = streams_root.expanduser().resolve()
        self.toolchain_factory = toolchain_factory
        self.process_factory = process_factory
        self.probe_factory = probe_factory
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

    def create_stream(
        self,
        source_rtsp_url: str,
        conservative_rtsp_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        source_url = _validate_rtsp_url(source_rtsp_url)
        if conservative_rtsp_url and conservative_rtsp_url.strip() != source_url:
            raise ValueError("V1.6 实时预览只需要一个原始 RTSP 地址")
        with self._lock:
            active_count = sum(
                1 for stream in self._streams.values() if stream.status in {"starting", "running"}
            )
            if active_count >= self.max_active_streams:
                raise StreamLimitExceeded("已有实时预览正在运行，请先停止当前拉流")

        toolchain = self.toolchain_factory()
        masked_url = _mask_rtsp_url(source_url)
        source_probe = self._probe_stream(toolchain, source_url, masked_url)
        width = source_probe.get("width")
        height = source_probe.get("height")
        fps = source_probe.get("fps")
        if not source_probe.get("ok") or not width or not height or not fps:
            detail = source_probe.get("error") or "无法获得 RTSP 分辨率和帧率"
            raise ValueError(f"RTSP 探测失败：{detail}")

        stream_id = uuid.uuid4().hex
        stream_dir = self.streams_root / stream_id
        stream_dir.mkdir(parents=True, exist_ok=True)
        outputs: Dict[str, StreamOutput] = {}
        for variant, title in (
            ("h264_native", "H.264 原生参数编码"),
            ("h265_optimized", "H.265 编码参数优化"),
        ):
            preview_dir = stream_dir / variant
            preview_dir.mkdir(parents=True, exist_ok=True)
            outputs[variant] = StreamOutput(
                variant=variant,
                title=title,
                preview_dir=preview_dir,
                probe=self._encoded_probe(source_probe, variant),
            )
        frame_queue_capacity = _frame_queue_capacity(int(width), int(height), float(fps))
        stream = LiveStream(
            stream_id=stream_id,
            source_url=source_url,
            masked_url=masked_url,
            stream_dir=stream_dir,
            source_probe=source_probe,
            outputs=outputs,
            frame_queue=queue.Queue(maxsize=frame_queue_capacity),
            frame_queue_capacity=frame_queue_capacity,
        )
        with self._lock:
            self._streams[stream_id] = stream
        self._start_pipeline(toolchain, stream)
        return self.get_status(stream_id)

    def get_status(self, stream_id: str) -> Dict[str, Any]:
        with self._lock:
            stream = self._stream_locked(stream_id)
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
        relative = PurePath(filename)
        parts = relative.parts
        if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
            raise StreamNotFound(filename)
        variant, leaf = parts
        if variant not in LIVE_VARIANTS or PurePath(leaf).name != leaf:
            raise StreamNotFound(filename)
        if Path(leaf).suffix.lower() not in HLS_ALLOWED_SUFFIXES:
            raise StreamNotFound(filename)
        with self._lock:
            stream = self._stream_locked(stream_id)
            self._refresh_status_locked(stream)
            output = stream.outputs.get(variant)
            if output is None:
                raise StreamNotFound(filename)
            path = (output.preview_dir / leaf).resolve()
            if not _is_inside(path, output.preview_dir) or not path.is_file():
                raise StreamNotReady(filename)
            return path

    def record_native_bytes(
        self,
        stream_id: str,
        variant: str,
        byte_count: int,
        now: Optional[float] = None,
    ) -> None:
        with self._lock:
            stream = self._stream_locked(stream_id)
            output = stream.outputs.get(variant)
            if output is None:
                raise StreamNotFound(variant)
        output.bitrate.add(byte_count, now=now)

    def _start_pipeline(self, toolchain: Toolchain, stream: LiveStream) -> None:
        try:
            for output in stream.outputs.values():
                output.preview_process = self._spawn_preview(
                    self._preview_command(toolchain, stream, output)
                )
            for output in stream.outputs.values():
                output.encoder_process = self._spawn_encoder(
                    self._native_encoder_command(toolchain, stream, output)
                )
                output.started_at = _now()
            stream.ingest_process = self._spawn_ingest(self._ingest_command(toolchain, stream))
        except Exception as exc:
            self._mark_failed(stream, str(exc))
            return

        stream.started_at = _now()
        stream.updated_at = stream.started_at
        if not self.enable_io_threads:
            return

        self._start_thread(stream, self._read_ingest_frames, stream)
        self._start_thread(stream, self._fanout_frames, stream)
        self._start_thread(stream, self._consume_process_log, stream, None, "ingest")
        for output in stream.outputs.values():
            self._start_thread(stream, self._relay_native_stream, stream, output)
            self._start_thread(stream, self._consume_process_log, stream, output, "encoder")
            self._start_thread(stream, self._consume_process_log, stream, output, "preview")

    def _spawn_ingest(self, command: List[Any]) -> subprocess.Popen:
        return self.process_factory(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _spawn_encoder(self, command: List[Any]) -> subprocess.Popen:
        return self.process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _spawn_preview(self, command: List[Any]) -> subprocess.Popen:
        return self.process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def _start_thread(self, stream: LiveStream, target: Callable, *args: Any) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        stream.threads.append(thread)
        thread.start()

    def _read_ingest_frames(self, stream: LiveStream) -> None:
        process = stream.ingest_process
        stdout = getattr(process, "stdout", None)
        width = int(stream.source_probe["width"])
        height = int(stream.source_probe["height"])
        frame_size = width * height * 3 // 2
        if stdout is None:
            self._mark_failed(stream, "RTSP 解码进程没有输出管道")
            return
        while not stream.stop_event.is_set():
            frame = self._read_exact(stdout, frame_size)
            if len(frame) != frame_size:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, "RTSP 解码输出已中断")
                return
            with self._lock:
                stream.source_frames += 1
            while not stream.stop_event.is_set():
                try:
                    stream.frame_queue.put(frame, timeout=0.5)
                    break
                except queue.Full:
                    continue

    def _fanout_frames(self, stream: LiveStream) -> None:
        while not stream.stop_event.is_set():
            try:
                frame = stream.frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                for variant in LIVE_VARIANTS:
                    process = stream.outputs[variant].encoder_process
                    stdin = getattr(process, "stdin", None)
                    if stdin is None:
                        raise BrokenPipeError(f"{variant} 编码器输入管道不可用")
                    stdin.write(frame)
                with self._lock:
                    stream.delivered_frames += 1
            except (BrokenPipeError, OSError, ValueError) as exc:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, f"原生编码帧分发失败：{exc}")
                return

    def _relay_native_stream(self, stream: LiveStream, output: StreamOutput) -> None:
        encoder_stdout = getattr(output.encoder_process, "stdout", None)
        preview_stdin = getattr(output.preview_process, "stdin", None)
        if encoder_stdout is None or preview_stdin is None:
            self._mark_failed(stream, f"{output.variant} 原生码流管道不可用")
            return
        while not stream.stop_event.is_set():
            chunk = encoder_stdout.read(4 * 1024)
            if not chunk:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, f"{output.variant} 原生编码输出已中断")
                return
            output.bitrate.add(len(chunk))
            try:
                preview_stdin.write(chunk)
                preview_stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                if not stream.stop_event.is_set():
                    self._mark_failed(stream, f"{output.variant} 等价预览输入失败：{exc}")
                return

    def _consume_process_log(
        self,
        stream: LiveStream,
        output: Optional[StreamOutput],
        role: str,
    ) -> None:
        process = stream.ingest_process if role == "ingest" else getattr(output, f"{role}_process")
        stderr = getattr(process, "stderr", None)
        if stderr is None:
            return
        for raw_line in stderr:
            if stream.stop_event.is_set():
                return
            line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
            cleaned = line.strip().replace(stream.source_url, stream.masked_url)
            if not cleaned:
                continue
            with self._lock:
                if output is None:
                    continue
                target = output.preview_log_tail if role == "preview" else output.log_tail
                target.append(cleaned)
                del target[:-20]
                if role == "encoder":
                    self._parse_progress(output, cleaned)

    def _parse_progress(self, output: StreamOutput, line: str) -> None:
        if "=" not in line:
            return
        key, value = line.split("=", 1)
        try:
            if key == "frame":
                output.encoded_frames = int(value.strip())
            elif key == "fps":
                output.encode_fps = float(value.strip())
            elif key == "speed":
                output.encode_speed_x = float(value.strip().rstrip("x"))
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

    def _mark_failed(self, stream: LiveStream, message: str) -> None:
        with self._lock:
            if stream.status in {"failed", "stopped"}:
                return
            stream.status = "failed"
            stream.error = message.replace(stream.source_url, stream.masked_url)
            stream.stopped_at = _now()
            stream.updated_at = stream.stopped_at
            stream.stop_event.set()
            for output in stream.outputs.values():
                if output.status != "stopped":
                    output.status = "failed"
                    output.error = stream.error
        threading.Thread(target=self._terminate_pipeline, args=(stream,), daemon=True).start()

    def _refresh_status_locked(self, stream: LiveStream) -> None:
        if stream.status in {"failed", "stopped"}:
            return
        processes = [stream.ingest_process]
        for output in stream.outputs.values():
            processes.extend([output.encoder_process, output.preview_process])
        for process in processes:
            if process is not None and process.poll() is not None:
                self._mark_failed(stream, "实时编码子进程意外退出")
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

    def _terminate_pipeline(self, stream: LiveStream) -> None:
        with self._lock:
            if stream.cleanup_started:
                return
            stream.cleanup_started = True
        stream.stop_event.set()
        processes: List[Optional[subprocess.Popen]] = [stream.ingest_process]
        for output in stream.outputs.values():
            processes.extend([output.encoder_process, output.preview_process])
        for process in processes:
            self._close_pipe(getattr(process, "stdin", None))
        for process in processes:
            self._stop_process(process)

    def _close_pipe(self, pipe: Any) -> None:
        if pipe is None:
            return
        try:
            pipe.close()
        except (OSError, ValueError):
            pass

    def _stop_process(self, process: Optional[subprocess.Popen]) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

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
                    self.stop_stream(stream_id, "页面心跳超时，实时编码已自动停止")
                except StreamNotFound:
                    pass

    def _stream_locked(self, stream_id: str) -> LiveStream:
        stream = self._streams.get(stream_id)
        if stream is None:
            raise StreamNotFound(stream_id)
        return stream

    def _encoded_probe(self, source_probe: Dict[str, Any], variant: str) -> Dict[str, Any]:
        probe = dict(source_probe)
        if variant == "h264_native":
            probe.update({
                "codec": "h264",
                "codec_long_name": "H.264 / AVC",
                "crf": None,
                "crf_label": "原生默认",
                "encoder": "libx264",
            })
        else:
            probe.update({
                "codec": "hevc",
                "codec_long_name": "H.265 / HEVC",
                "crf": HEVC_FIXED_CRF,
                "crf_label": f"{HEVC_FIXED_CRF:.1f}",
                "encoder": "libx265",
                "preset": HEVC_FIXED_PRESET,
            })
        return probe

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
            "stream=codec_name,codec_long_name,profile,width,height,pix_fmt,r_frame_rate,avg_frame_rate,bit_rate,field_order",
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
            return {"ok": False, "error": str(exc).replace(url, masked_url)}
        stdout = completed.stdout or ""
        stderr = (completed.stderr or "").strip().replace(url, masked_url)
        try:
            payload = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return {"ok": False, "error": stderr or "FFprobe 输出不是 JSON"}
        if getattr(completed, "returncode", 0) not in (0, None):
            return {"ok": False, "error": stderr or "FFprobe 探测失败"}
        streams = payload.get("streams") or []
        video = streams[0] if streams else {}
        fmt = payload.get("format") or {}
        fps = _fps_from_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "")
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
            "already_encoded": video.get("codec_name") not in {None, "", "rawvideo"},
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
            "-pix_fmt",
            "yuv420p",
            "-f",
            "rawvideo",
            "pipe:1",
        ]

    def _native_encoder_command(
        self,
        toolchain: Toolchain,
        stream: LiveStream,
        output: StreamOutput,
    ) -> List[Any]:
        width = int(stream.source_probe["width"])
        height = int(stream.source_probe["height"])
        fps = float(stream.source_probe["fps"])
        command: List[Any] = [
            toolchain.ffmpeg,
            "-hide_banner",
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
        ]
        if output.variant == "h264_native":
            command.extend(["-c:v", "libx264", "-f", "h264"])
        else:
            command.extend([
                "-c:v",
                "libx265",
                "-preset",
                HEVC_FIXED_PRESET,
                "-crf",
                f"{HEVC_FIXED_CRF:.1f}",
                "-profile:v",
                "main",
                "-pix_fmt",
                "yuv420p",
                "-x265-params",
                self._h265_fixed_params(fps),
                "-f",
                "hevc",
            ])
        command.extend(["-flush_packets", "1", "-progress", "pipe:2", "pipe:1"])
        return command

    def _preview_command(
        self,
        toolchain: Toolchain,
        stream: LiveStream,
        output: StreamOutput,
    ) -> List[Any]:
        fps = float(stream.source_probe["fps"])
        gop = max(1, round(fps))
        input_format = "h264" if output.variant == "h264_native" else "hevc"
        return [
            toolchain.ffmpeg,
            "-hide_banner",
            "-fflags",
            "+genpts",
            "-r",
            f"{fps:.6f}",
            "-f",
            input_format,
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            PREVIEW_PRESET,
            "-crf",
            str(PREVIEW_CRF),
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
            "1",
            "-hls_list_size",
            "20",
            "-hls_flags",
            "delete_segments+omit_endlist+independent_segments",
            "-hls_segment_filename",
            output.segment_pattern,
            output.playlist_path,
        ]

    def _h265_fixed_params(self, fps: Optional[float]) -> str:
        plan = v1_comparison_plan("aggressive")
        return plan.optimized.x265_params(fps or 25.0)
