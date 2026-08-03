"""V2.2.1 Remote Stable 实时源码直流工具。"""

import sys
from typing import Optional

from ..errors import ToolError
from ..tools import PROJECT_ROOT, Toolchain, check_capabilities, discover_toolchain
from .app import create_app
from .streams import LiveStreamManager


def run_web_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    toolchain: Optional[Toolchain] = None,
) -> None:
    if host != "127.0.0.1":
        raise ValueError("本地网页服务只允许监听 127.0.0.1")
    if port < 1 or port > 65535:
        raise ValueError("端口必须在 1 到 65535 之间")
    try:
        import uvicorn
    except ImportError as exc:
        raise ToolError("缺少 uvicorn，请先安装项目 Web 依赖") from exc

    active_toolchain = toolchain or discover_toolchain()
    check_capabilities(active_toolchain)
    if sys.platform.startswith("win"):
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    stream_manager = LiveStreamManager(
        streams_root=PROJECT_ROOT / "work" / "live_streams",
        toolchain_factory=lambda: active_toolchain,
    )
    try:
        uvicorn.run(
            create_app(stream_manager=stream_manager),
            host=host,
            port=port,
        )
    finally:
        stream_manager.close()
