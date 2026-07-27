import json
import os
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from ..core.models import Toolchain
from ..tools import discover_toolchain


LIVE_STREAM_STATUSES = ("starting", "running", "failed", "stopped")
HLS_PLAYLIST = "live.m3u8"
HLS_SEGMENT_PATTERN = "segment_%05d.ts"
HLS_ALLOWED_SUFFIXES = {".m3u8", ".ts"}
LIVE_VARIANTS = ("source", "conservative")

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
        if den == 0:
            return None
        return float(numerator) / den
    except ValueError:
        return None


@dataclass
class StreamOutput:
    variant: str
    title: str
    rtsp_url: str
    masked_url: str
    stream_dir: Path
    metric_dir: Path
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    metric_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    status: str = "starting"
    metric_status: str = "starting"
    error: Optional[str] = None
    metric_error: Optional[str] = None
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    log_tail: List[str] = field(default_factory=list)
    metric_log_tail: List[str] = field(default_factory=list)
    log_thread: Optional[threading.Thread] = field(default=None, repr=False)
    metric_log_thread: Optional[threading.Thread] = field(default=None, repr=False)
    metrics: Dict[str, Any] = field(default_factory=dict)
    probe: Dict[str, Any] = field(default_factory=dict)
    preview_mode: str = "starting"

    @property
    def playlist_path(self) -> Path:
        return self.stream_dir / HLS_PLAYLIST

    @property
    def segment_pattern(self) -> Path:
        return self.stream_dir / HLS_SEGMENT_PATTERN

    @property
    def metric_playlist_path(self) -> Path:
        return self.metric_dir / HLS_PLAYLIST

    @property
    def metric_segment_pattern(self) -> Path:
        return self.metric_dir / HLS_SEGMENT_PATTERN


@dataclass
class LiveStream:
    stream_id: str
    stream_dir: Path
    single_input_debug: bool = False
    status: str = "starting"
    error: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    outputs: Dict[str, StreamOutput] = field(default_factory=dict)

    def playlist_url(self, variant: str) -> Optional[str]:
        output = self.outputs.get(variant)
        if not output or output.status != "running":
            return None
        return f"/api/streams/{self.stream_id}/hls/{variant}/{HLS_PLAYLIST}"

    def bandwidth_saving_pct(self) -> Optional[float]:
        source = self.outputs.get("source")
        conservative = self.outputs.get("conservative")
        if source is None or conservative is None:
            return None
        source_mbps = source.metrics.get("camera_bitrate_mbps")
        conservative_mbps = conservative.metrics.get("camera_bitrate_mbps")
        if not isinstance(source_mbps, (int, float)) or source_mbps <= 0:
            return None
        if not isinstance(conservative_mbps, (int, float)):
            return None
        return ((source_mbps - conservative_mbps) / source_mbps) * 100

    def public_status(self) -> Dict[str, Any]:
        source_url = self.playlist_url("source")
        conservative_url = self.playlist_url("conservative")
        masked_urls = {
            variant: output.masked_url for variant, output in self.outputs.items()
        }
        probes = {
            variant: dict(output.probe) for variant, output in self.outputs.items()
        }
        return {
            "stream_id": self.stream_id,
            "status": self.status,
            "masked_url": " / ".join(
                f"{variant}: {masked_urls.get(variant, '--')}" for variant in LIVE_VARIANTS
            ),
            "masked_urls": masked_urls,
            "playlist_url": source_url,
            "source_playlist_url": source_url,
            "conservative_playlist_url": conservative_url,
            "probe": probes.get("source", {}),
            "probes": probes,
            "bandwidth_saving_pct": self.bandwidth_saving_pct(),
            "saving_basis": "camera_input_packet_bitrate",
            "single_input_debug": self.single_input_debug,
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
                    "metric_status": output.metric_status,
                    "playlist_url": self.playlist_url(variant),
                    "error": output.error,
                    "metric_error": output.metric_error,
                    "preview_mode": output.preview_mode,
                    "probe": dict(output.probe),
                    "metrics": dict(output.metrics),
                    "log_tail": list(output.log_tail[-5:]),
                    "metric_log_tail": list(output.metric_log_tail[-5:]),
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
                lines.extend(f"{variant} metric: {line}" for line in output.metric_log_tail[-1:])
        return lines[-6:]


class LiveStreamManager:
    def __init__(
        self,
        streams_root: Path,
        toolchain_factory: ToolchainFactory = discover_toolchain,
        process_factory: ProcessFactory = subprocess.Popen,
        probe_factory: ProbeFactory = subprocess.run,
        max_active_streams: int = 1,
    ) -> None:
        self.streams_root = streams_root.expanduser().resolve()
        self.toolchain_factory = toolchain_factory
        self.process_factory = process_factory
        self.probe_factory = probe_factory
        self.max_active_streams = max_active_streams
        self.streams_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._streams: Dict[str, LiveStream] = {}

    def close(self) -> None:
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
        conservative_url = _validate_rtsp_url(conservative_rtsp_url or source_rtsp_url)
        with self._lock:
            active_count = sum(
                1 for stream in self._streams.values() if stream.status in {"starting", "running"}
            )
            if active_count >= self.max_active_streams:
                raise StreamLimitExceeded("已有实时预览正在运行，请先停止当前拉流")
            stream_id = uuid.uuid4().hex
            stream_dir = self.streams_root / stream_id
            stream_dir.mkdir(parents=True, exist_ok=True)
            stream = LiveStream(
                stream_id=stream_id,
                stream_dir=stream_dir,
                single_input_debug=conservative_rtsp_url is None,
            )
            stream.outputs = {
                "source": StreamOutput(
                    "source",
                    "原生 H.264 摄像头流",
                    source_url,
                    _mask_rtsp_url(source_url),
                    stream_dir / "source",
                    stream_dir / "metrics" / "source",
                ),
                "conservative": StreamOutput(
                    "conservative",
                    "H.265 保守策略摄像头流",
                    conservative_url,
                    _mask_rtsp_url(conservative_url),
                    stream_dir / "conservative",
                    stream_dir / "metrics" / "conservative",
                ),
            }
            for output in stream.outputs.values():
                output.stream_dir.mkdir(parents=True, exist_ok=True)
                output.metric_dir.mkdir(parents=True, exist_ok=True)
            self._streams[stream_id] = stream
        self._start_processes(stream)
        return self.get_status(stream_id)

    def get_status(self, stream_id: str) -> Dict[str, Any]:
        with self._lock:
            stream = self._stream_locked(stream_id)
            self._refresh_status_locked(stream)
            return stream.public_status()

    def stop_stream(self, stream_id: str) -> Dict[str, Any]:
        with self._lock:
            stream = self._stream_locked(stream_id)
            outputs = list(stream.outputs.values())
            if stream.status == "stopped":
                return stream.public_status()
            stream.status = "stopped"
            stream.stopped_at = _now()
            stream.updated_at = stream.stopped_at
            for output in outputs:
                output.status = "stopped"
                output.metric_status = "stopped"
                output.stopped_at = stream.stopped_at
        for output in outputs:
            self._stop_process(output.process)
            self._stop_process(output.metric_process)
        with self._lock:
            stream = self._stream_locked(stream_id)
            for output in stream.outputs.values():
                output.process = None
                output.metric_process = None
            self._cleanup_stream_dir(stream)
            return stream.public_status()

    def get_hls_file(self, stream_id: str, filename: str) -> Path:
        relative = PurePath(filename)
        parts = relative.parts
        if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
            raise StreamNotFound(filename)
        variant, leaf = parts
        if variant not in LIVE_VARIANTS or PurePath(leaf).name != leaf:
            raise StreamNotFound(filename)
        suffix = Path(leaf).suffix.lower()
        if suffix not in HLS_ALLOWED_SUFFIXES:
            raise StreamNotFound(filename)
        with self._lock:
            stream = self._stream_locked(stream_id)
            self._refresh_status_locked(stream)
            output = stream.outputs.get(variant)
            if output is None:
                raise StreamNotFound(filename)
            path = (output.stream_dir / leaf).resolve()
            if not _is_inside(path, output.stream_dir) or not path.is_file():
                raise StreamNotReady(filename)
            return path

    def _start_processes(self, stream: LiveStream) -> None:
        toolchain = self.toolchain_factory()
        for variant in LIVE_VARIANTS:
            output = stream.outputs[variant]
            output.probe = self._probe_stream(toolchain, output)
            metric_command = self._metric_command(toolchain, output)
            preview_command = self._preview_command(toolchain, output)
            self._start_output(stream, variant, "metric", metric_command)
            self._start_output(stream, variant, "preview", preview_command)
        with self._lock:
            stream.started_at = _now()
            stream.updated_at = stream.started_at

    def _probe_stream(self, toolchain: Toolchain, output: StreamOutput) -> Dict[str, Any]:
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
            "format=format_name,duration,bit_rate",
            "-of",
            "json",
            output.rtsp_url,
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
            return {"ok": False, "error": str(exc)}
        stdout = completed.stdout or ""
        stderr = (completed.stderr or "").strip().replace(output.rtsp_url, output.masked_url)
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

    def _metric_command(self, toolchain: Toolchain, output: StreamOutput) -> List[Any]:
        return [
            toolchain.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-i",
            output.rtsp_url,
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            *self._hls_args(output.metric_dir, output.metric_segment_pattern),
        ]

    def _preview_command(self, toolchain: Toolchain, output: StreamOutput) -> List[Any]:
        codec = str(output.probe.get("codec") or "").lower()
        command: List[Any] = [
            toolchain.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-i",
            output.rtsp_url,
            "-map",
            "0:v:0",
            "-an",
        ]
        if codec == "h264":
            output.preview_mode = "copy"
            command.extend(["-c:v", "copy"])
        else:
            output.preview_mode = "h264_preview_transcode"
            command.extend([
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",
            ])
        command.extend(self._hls_args(output.stream_dir, output.segment_pattern))
        return command

    def _hls_args(self, output_dir: Path, segment_pattern: Path) -> List[Any]:
        return [
            "-f",
            "hls",
            "-hls_time",
            "2",
            "-hls_list_size",
            "4",
            "-hls_flags",
            "delete_segments+omit_endlist",
            "-hls_segment_filename",
            segment_pattern,
            output_dir / HLS_PLAYLIST,
        ]

    def _start_output(
        self,
        stream: LiveStream,
        variant: str,
        role: str,
        command: List[Any],
    ) -> None:
        output = stream.outputs[variant]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            process = self.process_factory(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except Exception as exc:
            with self._lock:
                if role == "metric":
                    output.metric_status = "failed"
                    output.metric_error = str(exc)
                else:
                    output.status = "failed"
                    output.error = str(exc)
                    stream.status = "failed"
                    stream.error = str(exc)
                stream.updated_at = _now()
            return
        with self._lock:
            if role == "metric":
                output.metric_process = process
            else:
                output.process = process
                output.started_at = _now()
        thread = threading.Thread(
            target=self._consume_stderr,
            args=(stream.stream_id, variant, role, process),
            daemon=True,
        )
        with self._lock:
            if role == "metric":
                output.metric_log_thread = thread
            else:
                output.log_thread = thread
        thread.start()

    def _consume_stderr(
        self,
        stream_id: str,
        variant: str,
        role: str,
        process: subprocess.Popen,
    ) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            with self._lock:
                stream = self._streams.get(stream_id)
                if stream is None:
                    return
                output = stream.outputs.get(variant)
                if output is None:
                    return
                cleaned = line.strip().replace(output.rtsp_url, output.masked_url)
                if not cleaned:
                    continue
                if role == "metric":
                    output.metric_log_tail.append(cleaned)
                    output.metric_log_tail = output.metric_log_tail[-20:]
                else:
                    output.log_tail.append(cleaned)
                    output.log_tail = output.log_tail[-20:]
                stream.updated_at = _now()

    def _refresh_status_locked(self, stream: LiveStream) -> None:
        if stream.status == "stopped":
            return
        failures: List[str] = []
        running_count = 0
        for output in stream.outputs.values():
            process = output.process
            metric_process = output.metric_process
            if output.status not in {"failed", "stopped"} and output.playlist_path.exists():
                output.status = "running"
                output.error = None
            if output.metric_status not in {"failed", "stopped"} and output.metric_playlist_path.exists():
                output.metric_status = "running"
                output.metric_error = None
            if output.status not in {"failed", "stopped"} and process is not None and process.poll() is not None:
                output.status = "failed"
                output.error = output.log_tail[-1] if output.log_tail else "FFmpeg 预览进程已退出"
                output.stopped_at = _now()
            if (
                output.metric_status not in {"failed", "stopped"}
                and metric_process is not None
                and metric_process.poll() is not None
            ):
                output.metric_status = "failed"
                output.metric_error = (
                    output.metric_log_tail[-1] if output.metric_log_tail else "FFmpeg 计量进程已退出"
                )
            output.metrics = self._combined_metrics(output)
            if output.status == "failed":
                failures.append(f"{output.variant}: {output.error or 'unknown error'}")
            elif output.status == "running":
                running_count += 1
        if failures:
            stopped_at = _now()
            for output in stream.outputs.values():
                if output.status not in {"failed", "stopped"}:
                    self._stop_process(output.process)
                    output.process = None
                    output.status = "stopped"
                    output.stopped_at = stopped_at
                if output.metric_status not in {"failed", "stopped"}:
                    self._stop_process(output.metric_process)
                    output.metric_process = None
                    output.metric_status = "stopped"
            stream.status = "failed"
            stream.error = failures[-1]
            stream.stopped_at = stopped_at
        elif running_count == len(stream.outputs):
            stream.status = "running"
            stream.error = None
        else:
            stream.status = "starting"
            stream.error = None
        stream.updated_at = _now()

    def _combined_metrics(self, output: StreamOutput) -> Dict[str, Any]:
        camera = self._hls_metrics(output.metric_dir / HLS_PLAYLIST)
        preview = self._hls_metrics(output.playlist_path)
        camera_bitrate = camera.get("bitrate_mbps") or output.probe.get("declared_bitrate_mbps")
        return {
            "camera_bitrate_mbps": camera_bitrate,
            "camera_segment_count": camera.get("segment_count"),
            "camera_window_seconds": camera.get("window_seconds"),
            "preview_bitrate_mbps": preview.get("bitrate_mbps"),
            "preview_segment_count": preview.get("segment_count"),
            "preview_window_seconds": preview.get("window_seconds"),
            "bitrate_mbps": camera_bitrate,
        }

    def _hls_metrics(self, playlist_path: Path) -> Dict[str, Any]:
        if not playlist_path.is_file():
            return {
                "bitrate_mbps": None,
                "segment_count": 0,
                "window_seconds": None,
            }
        try:
            lines = playlist_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {
                "bitrate_mbps": None,
                "segment_count": 0,
                "window_seconds": None,
            }
        pending_duration: Optional[float] = None
        total_seconds = 0.0
        total_bytes = 0
        segment_count = 0
        output_dir = playlist_path.parent
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#EXTINF:"):
                value = line.removeprefix("#EXTINF:").split(",", 1)[0]
                try:
                    pending_duration = float(value)
                except ValueError:
                    pending_duration = None
                continue
            if line.startswith("#") or pending_duration is None:
                continue
            leaf = PurePath(line).name
            if leaf != line or Path(leaf).suffix.lower() != ".ts":
                pending_duration = None
                continue
            segment_path = output_dir / leaf
            if segment_path.is_file():
                total_seconds += pending_duration
                total_bytes += segment_path.stat().st_size
                segment_count += 1
            pending_duration = None
        bitrate_mbps = None
        if total_seconds > 0 and total_bytes > 0:
            bitrate_mbps = (total_bytes * 8) / total_seconds / 1_000_000
        return {
            "bitrate_mbps": bitrate_mbps,
            "segment_count": segment_count,
            "window_seconds": total_seconds if segment_count else None,
        }

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

    def _stream_locked(self, stream_id: str) -> LiveStream:
        stream = self._streams.get(stream_id)
        if stream is None:
            raise StreamNotFound(stream_id)
        return stream
