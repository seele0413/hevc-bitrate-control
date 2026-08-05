import argparse
import tempfile
from pathlib import Path

import uvicorn

from hevc_lab.tools import discover_toolchain
from hevc_lab.web.app import create_app
from hevc_lab.web.streams import LiveStreamManager
from tests.test_integration import synthetic_ingest_1080p, synthetic_probe_1080p


def main() -> None:
    parser = argparse.ArgumentParser(description="V2.2.2 浏览器合成流验收服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8131)
    args = parser.parse_args()

    toolchain = discover_toolchain()
    with tempfile.TemporaryDirectory(prefix="hbc-browser-fixture-") as temp:
        manager = LiveStreamManager(
            streams_root=Path(temp) / "streams",
            toolchain_factory=lambda: toolchain,
            probe_factory=synthetic_probe_1080p,
            ingest_command_factory=synthetic_ingest_1080p,
            heartbeat_timeout_seconds=180,
        )
        app = create_app(stream_manager=manager)
        try:
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        finally:
            manager.close()


if __name__ == "__main__":
    main()
