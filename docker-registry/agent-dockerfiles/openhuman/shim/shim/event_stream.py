"""SSE consumer for openhuman-core's `GET /events?client_id=...`.

Core publishes WebChannelEvents (text_delta / chat_done / chat_error / tool
lifecycle ...) on this stream, filtered server-side by `client_id`. The shim
opens one subscription per run, BEFORE firing `channel_web_chat`, so no early
delta can be missed (see `ws_server._execute_run`).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os

import httpx
from httpx_sse import aconnect_sse

log = logging.getLogger(__name__)


def _base_url() -> str:
    host = os.environ.get("OPENHUMAN_CORE_HOST", "127.0.0.1")
    port = os.environ.get("OPENHUMAN_CORE_PORT", "7788")
    return f"http://{host}:{port}"


@contextlib.asynccontextmanager
async def subscribe_events(
    client_id: str,
    *,
    base_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
):
    """Async context manager: `__aenter__` completes once the SSE connection
    is established (headers received); yields an async iterator of
    `(event_name, data_dict)`. Non-JSON / non-dict `data:` payloads are
    skipped — never raise on a garbled frame.

    `base_url` / `transport` are test seams; production uses env-derived
    defaults (same OPENHUMAN_CORE_* convention as rpc_client).
    """
    headers: dict[str, str] = {}
    token = os.environ.get("OPENHUMAN_CORE_TOKEN", "")
    if token:
        # /events is header-auth-exempt upstream (browser EventSource can't
        # set headers) — sending the bearer anyway is harmless and keeps us
        # correct if that exemption ever tightens.
        headers["Authorization"] = f"Bearer {token}"
    # No read timeout: the stream is long-lived and the server emits a
    # keep-alive comment every 10s; connect failures should surface fast.
    timeout = httpx.Timeout(None, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        async with aconnect_sse(
            client,
            "GET",
            f"{base_url or _base_url()}/events",
            params={"client_id": client_id},
            headers=headers,
        ) as source:

            async def gen():
                async for sse in source.aiter_sse():
                    try:
                        data = json.loads(sse.data)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(data, dict):
                        yield sse.event, data

            yield gen()
