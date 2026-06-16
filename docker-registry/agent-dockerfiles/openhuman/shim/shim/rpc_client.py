"""Thin async wrapper around OpenHuman's HTTP JSON-RPC endpoint.

OpenHuman exposes POST /rpc with JSON-RPC 2.0 semantics, bearer-token auth.
Method names follow `<namespace>.<verb>`; see
`src/openhuman/agent/schemas.rs` and `threads/schemas.rs` upstream.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx

log = logging.getLogger(__name__)


class OpenHumanRpcError(RuntimeError):
    """Non-2xx or JSON-RPC `error` field from openhuman-core."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"openhuman /rpc error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class OpenHumanRpcClient:
    """Caller for openhuman-core's /rpc endpoint.

    Token is loaded from $OPENHUMAN_CORE_TOKEN at construct time. The token
    is written by openhuman-core's `init_rpc_token()` at first boot; the
    container's supervisor.sh blocks until the file exists, then exports it.
    """

    def __init__(self) -> None:
        host = os.environ.get("OPENHUMAN_CORE_HOST", "127.0.0.1")
        port = os.environ.get("OPENHUMAN_CORE_PORT", "7788")
        self._base = f"http://{host}:{port}"
        self._token = os.environ.get("OPENHUMAN_CORE_TOKEN", "")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Invoke an RPC method, return its `result`. Raises on RPC error."""
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params or {},
        }
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self._base}/rpc"
        log.debug("→ %s %s", method, params)
        resp = await self._client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data and data["error"]:
            err = data["error"]
            raise OpenHumanRpcError(
                int(err.get("code", -1)),
                str(err.get("message", "<no message>")),
                err.get("data"),
            )
        return data.get("result")

    async def health(self) -> bool:
        """GET /health — unauthenticated, returns 2xx on liveness."""
        try:
            resp = await self._client.get(f"{self._base}/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
