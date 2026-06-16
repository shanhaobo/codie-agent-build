"""Entrypoint: read config, materialize OpenHuman's MCP servers, start WS."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from .ws_server import serve_forever


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("SHIM_LOG_LEVEL", "INFO"),
        format="[shim] %(asctime)s %(levelname)s %(message)s",
    )
    host = os.environ.get("SHIM_WS_HOST", "0.0.0.0")
    port = int(os.environ.get("SHIM_WS_PORT", "8001"))

    try:
        asyncio.run(serve_forever(host, port))
    except KeyboardInterrupt:
        logging.info("shim shutting down (SIGINT)")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
