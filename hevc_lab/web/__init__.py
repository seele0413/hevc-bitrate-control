"""本地四路编码验证台。"""

from pathlib import Path
import sys
from typing import Optional

from ..core.models import Toolchain
from ..errors import ToolError
from ..tools import check_capabilities, discover_toolchain
from .app import create_app
from .jobs import JobManager


def run_web_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    toolchain: Optional[Toolchain] = None,
) -> None:
    if host != "127.0.0.1":
        raise ValueError("本地网页服务只允许监听 127.0.0.1")
    if port < 1 or port > 65535:
        raise ValueError("端口必须在 1～65535 之间")
    try:
        import uvicorn
    except ImportError as exc:
        raise ToolError("缺少 uvicorn，请先安装项目 Web 依赖") from exc

    active_toolchain = toolchain or discover_toolchain()
    check_capabilities(active_toolchain)
    if sys.platform.startswith("win"):
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    project_root = Path(__file__).resolve().parents[2]
    app = create_app(
        JobManager(
            jobs_root=project_root / "work" / "web_jobs",
            roi_config_path=project_root / "configs" / "camera-entrance-roi.json",
            toolchain_factory=lambda: active_toolchain,
        )
    )
    uvicorn.run(app, host=host, port=port)
