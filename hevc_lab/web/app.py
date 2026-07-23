from contextlib import asynccontextmanager
from pathlib import Path, PurePath
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..tools import PROJECT_ROOT
from .jobs import JobManager, JobNotFound, JobNotReady, WEB_STAGE_ORDER


FRONTEND_ROOT = PROJECT_ROOT / "apps" / "web"
DEFAULT_JOBS_ROOT = PROJECT_ROOT / "work" / "web_jobs"
DEFAULT_ROI_CONFIG = PROJECT_ROOT / "configs" / "camera-entrance-roi.json"
ALLOWED_UPLOAD_EXTENSIONS = {".mp4", ".mkv"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


def create_app(manager: Optional[JobManager] = None) -> FastAPI:
    owns_manager = manager is None
    active_manager = manager or JobManager(
        jobs_root=DEFAULT_JOBS_ROOT,
        roi_config_path=DEFAULT_ROI_CONFIG,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs = active_manager
        try:
            yield
        finally:
            if owns_manager:
                active_manager.close(wait=False)

    app = FastAPI(
        title="H.265 四路编码本地验证台",
        version="0.11.0",
        lifespan=lifespan,
    )

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "listen": "127.0.0.1 only",
            "stages": WEB_STAGE_ORDER,
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

    app.mount(
        "/",
        StaticFiles(directory=str(FRONTEND_ROOT), html=True, check_dir=False),
        name="web",
    )
    return app
