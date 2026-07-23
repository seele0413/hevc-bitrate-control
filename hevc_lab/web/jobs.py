import json
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.models import Toolchain
from ..multi_encode import run_multi_encode
from ..tools import discover_toolchain
from .preview import generate_browser_preview


WEB_STAGE_ORDER = [
    "queued",
    "preparing_reference",
    "encoding_default",
    "searching_conservative",
    "validating_conservative",
    "searching_balanced",
    "validating_balanced",
    "searching_aggressive",
    "validating_aggressive",
    "generating_previews",
    "completed",
    "failed",
]

WEB_STAGE_TITLES = {
    "queued": "排队中",
    "preparing_reference": "准备参考画面",
    "encoding_default": "编码默认方案",
    "searching_conservative": "搜索保守综合策略",
    "validating_conservative": "验证保守综合策略",
    "searching_balanced": "搜索均衡综合策略",
    "validating_balanced": "验证均衡综合策略",
    "searching_aggressive": "搜索激进综合策略",
    "validating_aggressive": "验证激进综合策略",
    "generating_previews": "生成浏览器 H.264 预览",
    "completed": "已完成",
    "failed": "失败",
}

WEB_STAGE_PROGRESS = {
    "queued": 0,
    "preparing_reference": 5,
    "encoding_default": 18,
    "searching_conservative": 30,
    "validating_conservative": 40,
    "searching_balanced": 50,
    "validating_balanced": 60,
    "searching_aggressive": 70,
    "validating_aggressive": 80,
    "generating_previews": 92,
    "completed": 100,
    "failed": 100,
}

STRATEGY_DOWNLOADS = {
    "default_x265": "default_x265.mp4",
    "composite_conservative": "composite_conservative.mp4",
    "composite_balanced": "composite_balanced.mp4",
    "composite_aggressive": "composite_aggressive.mp4",
}

REPORT_DOWNLOADS = (
    "final_metrics.csv",
    "final_summary.md",
    "research_manifest.json",
)

ToolchainFactory = Callable[[], Toolchain]
Runner = Callable[..., Dict[str, Any]]
PreviewBuilder = Callable[[Toolchain, Path, Path], Dict[str, Any]]


class JobNotFound(KeyError):
    pass


class JobNotReady(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@dataclass
class WebJob:
    job_id: str
    original_filename: str
    upload_path: Path
    output_dir: Path
    preview_dir: Path
    status: str = "queued"
    error: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    files: Dict[str, Path] = field(default_factory=dict)
    previews: Dict[str, Path] = field(default_factory=dict)
    preview_errors: Dict[str, str] = field(default_factory=dict)

    @property
    def job_dir(self) -> Path:
        return self.output_dir.parent

    def public_status(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "original_filename": self.original_filename,
            "status": self.status,
            "stage_title": WEB_STAGE_TITLES.get(self.status, self.status),
            "progress": WEB_STAGE_PROGRESS.get(self.status, 0),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result_available": self.result is not None,
            "stages": [
                {"status": status, "title": WEB_STAGE_TITLES[status]}
                for status in WEB_STAGE_ORDER
            ],
        }

    def state_for_disk(self) -> Dict[str, Any]:
        payload = self.public_status()
        payload["paths"] = {
            "upload": str(self.upload_path),
            "output_dir": str(self.output_dir),
            "preview_dir": str(self.preview_dir),
        }
        payload["preview_errors"] = dict(self.preview_errors)
        return payload


class JobManager:
    def __init__(
        self,
        jobs_root: Path,
        roi_config_path: Path,
        toolchain_factory: ToolchainFactory = discover_toolchain,
        runner: Runner = run_multi_encode,
        preview_builder: PreviewBuilder = generate_browser_preview,
    ) -> None:
        self.jobs_root = jobs_root.expanduser().resolve()
        self.roi_config_path = roi_config_path.expanduser().resolve()
        self.toolchain_factory = toolchain_factory
        self.runner = runner
        self.preview_builder = preview_builder
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._jobs: Dict[str, WebJob] = {}
        self._futures: Dict[str, Future] = {}

    def close(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def reserve_job(self, original_filename: str, extension: str) -> WebJob:
        job_id = uuid.uuid4().hex
        job_dir = self.jobs_root / job_id
        upload_dir = job_dir / "upload"
        output_dir = job_dir / "results"
        preview_dir = job_dir / "previews"
        upload_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        preview_dir.mkdir(parents=True, exist_ok=True)
        job = WebJob(
            job_id=job_id,
            original_filename=original_filename,
            upload_path=upload_dir / f"input{extension.lower()}",
            output_dir=output_dir,
            preview_dir=preview_dir,
        )
        with self._lock:
            self._jobs[job_id] = job
            self._write_state_locked(job)
        return job

    def discard_job(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def enqueue(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._job_locked(job_id)
            future = self._executor.submit(self._run_job, job_id)
            self._futures[job_id] = future
            self._write_state_locked(job)
            return job.public_status()

    def get_status(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._job_locked(job_id).public_status()

    def get_results(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._job_locked(job_id)
            if job.result is None:
                raise JobNotReady(job.status)
            return job.result

    def get_file(self, job_id: str, filename: str) -> Path:
        with self._lock:
            job = self._job_locked(job_id)
            path = job.files.get(filename)
            if path is None:
                raise JobNotFound(filename)
            return path

    def get_preview(self, job_id: str, strategy_id: str) -> Path:
        with self._lock:
            job = self._job_locked(job_id)
            path = job.previews.get(strategy_id)
            if path is None:
                raise JobNotFound(strategy_id)
            return path

    def _run_job(self, job_id: str) -> None:
        toolchain = self.toolchain_factory()
        with self._lock:
            job = self._job_locked(job_id)
            job.started_at = _now()
            self._set_status_locked(job, "preparing_reference")

        def progress(stage: str) -> None:
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None:
                    return
                self._set_status_locked(current, stage)

        try:
            payload = self.runner(
                toolchain=toolchain,
                input_path=job.upload_path,
                roi_config_path=self.roi_config_path,
                output_dir=job.output_dir,
                progress_callback=progress,
            )
            with self._lock:
                self._set_status_locked(job, "generating_previews")
            files = self._collect_files(job, payload)
            previews, preview_errors = self._generate_previews(job, toolchain, payload)
            with self._lock:
                job.files = files
                job.previews = previews
                job.preview_errors = preview_errors
                job.result = self._public_result(job, payload)
                job.error = None
                job.completed_at = _now()
                self._set_status_locked(job, "completed")
        except Exception as exc:
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None:
                    return
                current.error = str(exc)
                current.completed_at = _now()
                self._set_status_locked(current, "failed")

    def _collect_files(self, job: WebJob, payload: Dict[str, Any]) -> Dict[str, Path]:
        files: Dict[str, Path] = {}
        for strategy in payload.get("strategies", []):
            if strategy.get("status") != "completed":
                continue
            expected_name = STRATEGY_DOWNLOADS.get(strategy.get("strategy_id"))
            output_path = strategy.get("output_path")
            if not expected_name or not output_path:
                continue
            path = Path(output_path).resolve()
            if path.name == expected_name and path.is_file() and _is_inside(path, job.output_dir):
                files[expected_name] = path
        for filename in REPORT_DOWNLOADS:
            path = (job.output_dir / filename).resolve()
            if path.is_file() and _is_inside(path, job.output_dir):
                files[filename] = path
        return files

    def _generate_previews(
        self,
        job: WebJob,
        toolchain: Toolchain,
        payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Path], Dict[str, str]]:
        previews: Dict[str, Path] = {}
        errors: Dict[str, str] = {}
        for strategy in payload.get("strategies", []):
            strategy_id = strategy.get("strategy_id")
            output_path = strategy.get("output_path")
            if strategy.get("status") != "completed" or not strategy_id or not output_path:
                continue
            source = Path(output_path).resolve()
            if not source.is_file() or not _is_inside(source, job.output_dir):
                continue
            destination = (job.preview_dir / f"{strategy_id}_preview.mp4").resolve()
            try:
                self.preview_builder(toolchain, source, destination)
                previews[strategy_id] = destination
            except Exception as exc:
                errors[strategy_id] = str(exc)
        return previews, errors

    def _public_result(self, job: WebJob, payload: Dict[str, Any]) -> Dict[str, Any]:
        public = {
            "schema_version": payload.get("schema_version"),
            "pipeline_version": payload.get("pipeline_version"),
            "study": payload.get("study"),
            "comparison_policy": payload.get("comparison_policy", {}),
            "input": self._public_video(payload.get("input", {})),
            "strategies": [],
            "downloads": {
                filename: f"/api/jobs/{job.job_id}/files/{filename}"
                for filename in job.files
            },
            "preview_notice": "H.264 预览只用于浏览器观看，不参与 H.265 正式指标",
            "preview_errors": dict(job.preview_errors),
        }
        for strategy in payload.get("strategies", []):
            item = self._public_strategy(job, strategy)
            public["strategies"].append(item)
        return public

    def _public_strategy(self, job: WebJob, strategy: Dict[str, Any]) -> Dict[str, Any]:
        item = {
            key: value
            for key, value in strategy.items()
            if key
            not in {
                "output_path",
                "short_search",
                "full_attempts",
                "roi",
                "denoise",
            }
        }
        strategy_id = strategy.get("strategy_id")
        filename = STRATEGY_DOWNLOADS.get(strategy_id)
        if filename in job.files:
            item["download_filename"] = filename
            item["download_url"] = f"/api/jobs/{job.job_id}/files/{filename}"
        else:
            item["download_filename"] = None
            item["download_url"] = None
        if strategy_id in job.previews:
            item["preview_url"] = f"/api/jobs/{job.job_id}/previews/{strategy_id}"
            item["preview_available"] = True
            item["preview_error"] = None
        else:
            item["preview_url"] = None
            item["preview_available"] = False
            item["preview_error"] = job.preview_errors.get(strategy_id)
        return item

    def _public_video(self, video: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in video.items() if key != "path"}

    def _job_locked(self, job_id: str) -> WebJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    def _set_status_locked(self, job: WebJob, status: str) -> None:
        job.status = status
        job.updated_at = _now()
        self._write_state_locked(job)

    def _write_state_locked(self, job: WebJob) -> None:
        state_path = job.job_dir / "job.json"
        temp_path = job.job_dir / "job.part.json"
        temp_path.write_text(
            json.dumps(job.state_for_disk(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(state_path)
