from contextlib import asynccontextmanager
from pathlib import Path, PurePath
from typing import Dict, Optional

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..multi_encode import MULTI_ENCODE_PIPELINE_VERSION
from ..tools import PROJECT_ROOT
from .jobs import (
    JobManager,
    JobNotFound,
    JobNotReady,
    STRATEGY_DOWNLOADS,
    WEB_STAGE_ORDER,
)
from .streams import (
    HLS_PLAYLIST,
    LiveStreamManager,
    StreamLimitExceeded,
    StreamNotFound,
    StreamNotReady,
)


FRONTEND_ROOT = PROJECT_ROOT / "apps" / "demo_live"
DEFAULT_JOBS_ROOT = PROJECT_ROOT / "work" / "web_jobs"
DEFAULT_STREAMS_ROOT = PROJECT_ROOT / "work" / "live_streams"
DEFAULT_ROI_CONFIG = PROJECT_ROOT / "configs" / "camera-entrance-roi.json"
ALLOWED_UPLOAD_EXTENSIONS = {".mp4", ".mkv"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


def create_app(
    manager: Optional[JobManager] = None,
    stream_manager: Optional[LiveStreamManager] = None,
) -> FastAPI:
    owns_manager = manager is None
    owns_stream_manager = stream_manager is None
    active_manager = manager or JobManager(
        jobs_root=DEFAULT_JOBS_ROOT,
        roi_config_path=DEFAULT_ROI_CONFIG,
    )
    active_stream_manager = stream_manager or LiveStreamManager(
        streams_root=DEFAULT_STREAMS_ROOT,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs = active_manager
        app.state.streams = active_stream_manager
        try:
            yield
        finally:
            if owns_manager:
                active_manager.close(wait=False)
            if owns_stream_manager:
                active_stream_manager.close()

    app = FastAPI(
        title="H.265 四路编码本地验证台",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "listen": "127.0.0.1 only",
            "stages": WEB_STAGE_ORDER,
        }

    @app.get("/api/runtime")
    def runtime():
        return {
            "ok": True,
            "app_version": __version__,
            "pipeline_version": MULTI_ENCODE_PIPELINE_VERSION,
            "strategy_ids": list(STRATEGY_DOWNLOADS),
            "stages": WEB_STAGE_ORDER,
            "live_preview": {
                "enabled": True,
                "protocol": "dual RTSP camera streams to HLS preview",
                "playlist": HLS_PLAYLIST,
                "variants": ["source", "conservative"],
                "frontend": "apps/demo_live",
                "saving_basis": "camera_input_packet_bitrate",
            },
        }

    @app.post("/api/jobs", status_code=202)
    async def create_job(file: UploadFile = File(...)):
        original_name = file.filename or ""
        extension = Path(original_name).suffix.lower()
        if extension not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=400, detail="只支持上传 MP4 或 MKV 视频")
        job = active_manager.reserve_job(original_name, extension)
        total = 0
        try:
            with job.upload_path.open("wb") as stream:
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="上传视频过大")
                    stream.write(chunk)
            if total <= 0:
                raise HTTPException(status_code=400, detail="上传视频不能为空")
        except HTTPException:
            active_manager.discard_job(job.job_id)
            job.upload_path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        return active_manager.enqueue(job.job_id)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            return active_manager.get_status(job_id)
        except JobNotFound:
            raise HTTPException(status_code=404, detail="任务不存在")

    @app.get("/api/jobs/{job_id}/results")
    def get_results(job_id: str):
        try:
            return active_manager.get_results(job_id)
        except JobNotReady as exc:
            raise HTTPException(
                status_code=409,
                detail=f"任务尚未完成，当前状态：{exc}",
            )
        except JobNotFound:
            raise HTTPException(status_code=404, detail="任务不存在")

    @app.get("/api/jobs/{job_id}/files/{filename:path}")
    def download_file(job_id: str, filename: str):
        if PurePath(filename).name != filename:
            raise HTTPException(status_code=400, detail="文件名非法")
        try:
            path = active_manager.get_file(job_id, filename)
        except JobNotFound:
            raise HTTPException(status_code=404, detail="文件不存在")
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=filename,
        )

    @app.get("/api/jobs/{job_id}/previews/{strategy_id}")
    def preview_file(job_id: str, strategy_id: str):
        try:
            path = active_manager.get_preview(job_id, strategy_id)
        except JobNotFound:
            raise HTTPException(status_code=404, detail="预览不存在")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/api/streams", status_code=202)
    def create_stream(payload: Dict[str, str] = Body(...)):
        payload = payload or {}
        source_rtsp_url = payload.get("source_rtsp_url") or payload.get("rtsp_url") or ""
        conservative_rtsp_url = payload.get("conservative_rtsp_url")
        if not source_rtsp_url:
            raise HTTPException(status_code=400, detail="请输入原生 H.264 RTSP 地址")
        if "rtsp_url" not in payload and not conservative_rtsp_url:
            raise HTTPException(status_code=400, detail="请输入 H.265 保守策略 RTSP 地址")
        try:
            return active_stream_manager.create_stream(
                source_rtsp_url,
                conservative_rtsp_url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except StreamLimitExceeded as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get("/api/streams/{stream_id}")
    def get_stream(stream_id: str):
        try:
            return active_stream_manager.get_status(stream_id)
        except StreamNotFound:
            raise HTTPException(status_code=404, detail="拉流任务不存在")

    @app.delete("/api/streams/{stream_id}")
    def stop_stream(stream_id: str):
        try:
            return active_stream_manager.stop_stream(stream_id)
        except StreamNotFound:
            raise HTTPException(status_code=404, detail="拉流任务不存在")

    @app.get("/api/streams/{stream_id}/hls/{filename:path}")
    def hls_file(stream_id: str, filename: str):
        try:
            path = active_stream_manager.get_hls_file(stream_id, filename)
        except StreamNotReady:
            raise HTTPException(status_code=404, detail="HLS 文件尚未生成")
        except StreamNotFound:
            raise HTTPException(status_code=404, detail="HLS 文件不存在")
        media_type = (
            "application/vnd.apple.mpegurl"
            if path.suffix.lower() == ".m3u8"
            else "video/mp2t"
        )
        return FileResponse(path, media_type=media_type)

    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_ROOT), html=True, check_dir=False),
        name="web",
    )
    return app
