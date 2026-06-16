"""Shared fixtures: a real in-process shim WS server wired to fakes.

The shim's seams (mirroring production wiring in `serve_forever`):
  - `rpc`              — OpenHumanRpcClient        → FakeRpc here
  - `subscribe_events` — SSE consumer (event_stream) → queue-backed fake here

Tests speak real WebSocket frames against a real `websockets` server, so the
full parse → dispatch → respond/event path is exercised; only the two
HTTP-facing edges are faked.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from shim.ws_server import _serve_client


class FakeRpc:
    """Programmable stand-in for OpenHumanRpcClient.

    `responses[method]` is the value `.call()` returns. `calls` records
    (method, params, subscribed_at_call_time) — the third element lets tests
    assert the SSE subscription was opened BEFORE channel_web_chat fired
    (otherwise early deltas could be missed).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.responses: dict[str, Any] = {}
        self.subscribed = asyncio.Event()

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params or {}, self.subscribed.is_set()))
        return self.responses.get(method)

    async def health(self) -> bool:
        return True


def make_fake_events():
    """Queue-backed subscribe_events fake.

    Returns (queue, bind). Tests push `(event_name, data)` tuples; push `None`
    to end the stream. The seam contract (matching the real SSE consumer):
    `subscribe_events(client_id)` is an async context manager whose
    `__aenter__` completes once the event connection is OPEN and yields an
    async iterator of `(event_name, data)`. The fake flips `FakeRpc.subscribed`
    on enter so tests can assert subscribe-before-chat ordering.
    """
    q: asyncio.Queue = asyncio.Queue()

    def bind(rpc: FakeRpc | None = None):
        @contextlib.asynccontextmanager
        async def subscribe(client_id: str):
            if rpc is not None:
                rpc.subscribed.set()

            async def gen():
                while True:
                    item = await q.get()
                    if item is None:
                        return
                    yield item

            yield gen()

        return subscribe

    return q, bind


class ShimClient:
    """Thin test client: send a v1 request, await frames by predicate."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self._frames: list[dict[str, Any]] = []

    async def request(self, req_id: str, method: str, params: dict[str, Any]) -> None:
        await self.ws.send(json.dumps({
            'v': 1,
            'id': req_id,
            'type': 'request',
            'method': method,
            'params': params,
        }))

    async def next_frame(self, timeout: float = 5.0) -> dict[str, Any]:
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        frame = json.loads(raw)
        self._frames.append(frame)
        return frame

    async def frame_where(self, pred, timeout: float = 5.0) -> dict[str, Any]:
        async with asyncio.timeout(timeout):
            while True:
                frame = await self.next_frame(timeout)
                if pred(frame):
                    return frame


@contextlib.asynccontextmanager
async def shim_server(rpc: FakeRpc, subscribe_events):
    runs: dict[str, Any] = {}
    async with serve(
        lambda ws: _serve_client(ws, rpc, subscribe_events=subscribe_events, runs=runs),
        '127.0.0.1',
        0,
    ) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f'ws://127.0.0.1:{port}') as ws:
            yield ShimClient(ws)


@pytest.fixture
def chat_rpc() -> FakeRpc:
    """FakeRpc pre-programmed with a successful channel_web_chat accept.

    Mirrors the real RpcOutcome::single_log nesting:
    {"result": {...}, "logs": [...]} INSIDE the JSON-RPC result.
    """
    rpc = FakeRpc()
    rpc.responses['openhuman.channel_web_chat'] = {
        'result': {
            'accepted': True,
            'client_id': 'whatever',
            'thread_id': 'codie-bridge',
            'request_id': 'req-1',
        },
        'logs': ['web channel request accepted'],
    }
    return rpc
