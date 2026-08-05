from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import DIRECT_HEVC_HLS_CONFIG, HEVC_CONFIG
from ..tools import PROJECT_ROOT
from .streams import (
    HEARTBEAT_TIMEOUT_SECONDS,
    HLS_PLAYLIST,
    HLS_PLAYLIST_SEGMENTS,
    HLS_SEGMENT_SECONDS,
    HLS_TRANSPORT_MEASUREMENT_BASIS,
    H265_TARGET_DELAY_SECONDS,
    LIVE_VARIANTS,
    PLAYBACK_POLICY,
    PLAYBACK_RECOVERY_HIGH_WATERMARK_SECONDS,
    PLAYBACK_RECOVERY_LOW_WATERMARK_SECONDS,
    SOURCE_TARGET_DELAY_SECONDS,
    STREAM_PIPELINE_VERSION,
    LiveStreamManager,
    StreamLimitExceeded,
    StreamNotFound,
    StreamNotReady,
)


FRONTEND_ROOT = PROJECT_ROOT / "apps" / "web"
DEFAULT_STREAMS_ROOT = PROJECT_ROOT / "work" / "live_streams"
SAVING_BASIS = (
    "source_h264_elementary_stream_bytes_vs_"
    "h265_elementary_stream_bytes_rolling_30s"
)


def _rtsp_url_from_payload(payload: Dict[str, Any]) -> str:
    if set(payload) != {"rtsp_url"}:
        raise HTTPException(status_code=400, detail="请求只允许包含 rtsp_url 字段")
    rtsp_url = payload.get("rtsp_url")
    if not isinstance(rtsp_url, str) or not rtsp_url.strip():
        raise HTTPException(status_code=400, detail="请输入 RTSP 地址")
    return rtsp_url


def _hls_response_headers(suffix: str) -> Dict[str, str]:
    if suffix.lower() == ".m3u8":
        return {
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Accel-Buffering": "no",
        }
    return {
        "Cache-Control": "private, max-age=120",
        "X-Accel-Buffering": "no",
    }


def create_app(stream_manager: Optional[LiveStreamManager] = None) -> FastAPI:
    owns_stream_manager = stream_manager is None
    active_stream_manager = stream_manager or LiveStreamManager(
        streams_root=DEFAULT_STREAMS_ROOT,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.streams = active_stream_manager
        try:
            yield
        finally:
            if owns_stream_manager:
                active_stream_manager.close()

    app = FastAPI(
        title="V2.2.2 HEVC 直接播放实时工具",
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/runtime")
    def runtime():
        return {
            "ok": True,
            "app_version": __version__,
            "pipeline_version": STREAM_PIPELINE_VERSION,
            "commands": ["check-env", "web"],
            "live_preview": {
                "enabled": True,
                "input": "h264_rtsp_only",
                "variants": list(LIVE_VARIANTS),
                "frontend": "apps/web",
                "source_mode": "h264_elementary_stream_copy_to_hls",
                "h265_delivery_mode": (
                    "timestamped_mpegts_to_hevc_fmp4_hls_stream_copy"
                ),
                "hls_segment_types": {
                    "source": "mpegts",
                    "h265_optimized": DIRECT_HEVC_HLS_CONFIG.segment_type,
                },
                "h265_keyframe_bound_segments": True,
                "playlist": HLS_PLAYLIST,
                "saving_basis": SAVING_BASIS,
                "h265_config": HEVC_CONFIG.public_dict(),
                "hls_transport_measurement_basis": HLS_TRANSPORT_MEASUREMENT_BASIS,
                "playback": {
                    "policy": PLAYBACK_POLICY,
                    "source_target_delay_seconds": SOURCE_TARGET_DELAY_SECONDS,
                    "h265_target_delay_seconds": H265_TARGET_DELAY_SECONDS,
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
            },
        }

    @app.post("/api/streams", status_code=202)
    def create_stream(payload: Dict[str, Any] = Body(...)):
        rtsp_url = _rtsp_url_from_payload(payload or {})
        try:
            return active_stream_manager.create_stream(rtsp_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except StreamLimitExceeded as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/streams/{stream_id}")
    def get_stream(stream_id: str):
        try:
            return active_stream_manager.get_status(stream_id)
        except StreamNotFound as exc:
            raise HTTPException(status_code=404, detail="拉流任务不存在") from exc

    @app.delete("/api/streams/{stream_id}")
    def stop_stream(stream_id: str):
        try:
            return active_stream_manager.stop_stream(stream_id)
        except StreamNotFound as exc:
            raise HTTPException(status_code=404, detail="拉流任务不存在") from exc

    @app.post("/api/streams/{stream_id}/heartbeat")
    def heartbeat_stream(stream_id: str):
        try:
            return active_stream_manager.heartbeat(stream_id)
        except StreamNotFound as exc:
            raise HTTPException(status_code=404, detail="拉流任务不存在") from exc

    @app.post("/api/streams/{stream_id}/stop")
    def stop_stream_from_page(stream_id: str):
        try:
            return active_stream_manager.stop_stream(
                stream_id,
                "网页已关闭，实时处理已停止",
            )
        except StreamNotFound as exc:
            raise HTTPException(status_code=404, detail="拉流任务不存在") from exc

    @app.get("/api/streams/{stream_id}/hls/{filename:path}")
    def hls_file(stream_id: str, filename: str):
        try:
            path = active_stream_manager.get_hls_file(stream_id, filename)
        except StreamNotReady as exc:
            raise HTTPException(status_code=404, detail="HLS 文件尚未生成") from exc
        except StreamNotFound as exc:
            raise HTTPException(status_code=404, detail="HLS 文件不存在") from exc
        media_types = {
            ".m3u8": "application/vnd.apple.mpegurl",
            ".ts": "video/mp2t",
            ".m4s": "video/mp4",
            ".mp4": "video/mp4",
        }
        media_type = media_types.get(path.suffix.lower(), "application/octet-stream")
        try:
            file_snapshot = path.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="HLS 文件尚未就绪") from exc
        return Response(
            content=file_snapshot,
            media_type=media_type,
            headers=_hls_response_headers(path.suffix),
        )

    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_ROOT), html=True, check_dir=False),
        name="web",
    )
    return app
