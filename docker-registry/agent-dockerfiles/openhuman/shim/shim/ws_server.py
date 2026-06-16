"""WS server that speaks Bridge's Gateway RPC v1 over websockets.

Bridge dispatches openHuman through `sendViaGatewayRpcFresh`
(agent_dispatcher.dart), which expects the agentcore-style v1 protocol:

  - Inbound:  {"v":1,"id":"...","type":"request","method":"...","params":{...}}
  - Response: {"v":1,"type":"response","ref_id":"<req id>","result":...} or
              {..., "error":{"code":...,"message":"...","retryable":bool}}
  - Events:   {"v":1,"type":"event","event":"agent.delta","data":{"text":...}}
              plus terminal "agent.complete" {run_id, content} /
              "agent.error" {run_id, error}.

Turn driver: `openhuman.channel_web_chat` — async on the core side, returns a
`request_id` immediately and streams progress (text_delta / chat_done /
chat_error) on `GET /events?client_id=...` SSE. This replaced the original
`agent.chat` flow, which was synchronous AND ran without a progress sink, so
no streaming events ever reached /events (see Agent::run_single call in
`inference/local/ops.rs` — no set_on_progress).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

from .rpc_client import OpenHumanRpcClient, OpenHumanRpcError

log = logging.getLogger(__name__)

# Stable conversation key: every Bridge send lands on the same core thread so
# the agent keeps conversational context across turns (Bridge's chat page is a
# single per-instance conversation). Core persists it in the conversation
# JSONL under $OPENHUMAN_WORKSPACE (named volume — survives restarts).
THREAD_ID = "codie-bridge"

# In-process run registry. Container restart = fresh state; thread persistence
# is delegated to openhuman-core's own store (see THREAD_ID note above).
_runs: dict[str, "RunState"] = {}


@dataclass
class RunState:
    run_id: str
    status: str = "running"  # running | done | error
    content: str = ""
    error: str | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    # Keep a strong ref to the executor task (bare create_task is GC-able).
    task: asyncio.Task | None = None


class ShimError(Exception):
    """Dispatch error carrying the v1 `retryable` flag (Bridge fails fast on
    retryable=false, retries agent.wait otherwise)."""

    def __init__(self, code: int, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _response(req_id: str, result: Any) -> str:
    return json.dumps(
        {"v": 1, "type": "response", "ref_id": req_id, "id": req_id, "result": result}
    )


def _error(req_id: str, code: int, message: str, retryable: bool = False) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": "response",
            "ref_id": req_id,
            "id": req_id,
            "error": {"code": code, "message": message, "retryable": retryable},
        }
    )


def _event(event: str, data: dict[str, Any]) -> str:
    return json.dumps({"v": 1, "type": "event", "event": event, "data": data})


def unwrap_rpc_result(value: Any) -> Any:
    """Core handlers built on `RpcOutcome::single_log` serialize as
    {"result": <value>, "logs": [...]} inside the JSON-RPC result
    (`invocation_to_rpc_json`); plain outcomes are the value itself."""
    if isinstance(value, dict) and "result" in value and "logs" in value:
        return value["result"]
    return value


async def _send(ws: ServerConnection, frame: str) -> None:
    """Best-effort send: a client that vanished mid-run must not kill the
    run task (the run still completes server-side; agent.wait state stays
    correct for a reconnect)."""
    try:
        await ws.send(frame)
    except websockets.exceptions.ConnectionClosed:
        pass


async def _execute_run(
    run: RunState,
    message: str,
    rpc: OpenHumanRpcClient,
    ws: ServerConnection,
    subscribe_events,
) -> None:
    """Drive one agent turn: subscribe SSE first (no missed deltas), then
    start the run, then translate events until a terminal one arrives."""
    client_id = f"codie-{run.run_id}"
    try:
        async with subscribe_events(client_id) as events:
            res = unwrap_rpc_result(
                await rpc.call(
                    "openhuman.channel_web_chat",
                    {
                        "client_id": client_id,
                        "thread_id": THREAD_ID,
                        "message": message,
                    },
                )
            )
            request_id = str((res or {}).get("request_id") or "")
            async for name, data in events:
                if request_id and str(data.get("request_id") or "") != request_id:
                    continue
                if name == "text_delta":
                    txt = data.get("delta")
                    if isinstance(txt, str) and txt:
                        await _send(ws, _event("agent.delta", {"text": txt}))
                    continue
                if name == "chat_done":
                    run.content = str(data.get("full_response") or "")
                    run.status = "done"
                    await _send(
                        ws,
                        _event(
                            "agent.complete",
                            {"run_id": run.run_id, "content": run.content},
                        ),
                    )
                    return
                if name == "chat_error":
                    run.error = str(
                        data.get("message") or data.get("error_type") or "agent error"
                    )
                    run.status = "error"
                    await _send(
                        ws,
                        _event(
                            "agent.error",
                            {"run_id": run.run_id, "error": run.error},
                        ),
                    )
                    return
            run.status = "error"
            run.error = "event stream ended before run completion"
            await _send(
                ws, _event("agent.error", {"run_id": run.run_id, "error": run.error})
            )
    except Exception as e:
        log.exception("run %s failed", run.run_id)
        run.status = "error"
        run.error = f"{type(e).__name__}: {e}"
        await _send(
            ws, _event("agent.error", {"run_id": run.run_id, "error": run.error})
        )
    finally:
        run.done.set()


async def _dispatch(
    method: str,
    params: dict[str, Any],
    rpc: OpenHumanRpcClient,
    ws: ServerConnection,
    runs: dict[str, RunState],
    subscribe_events,
) -> Any:
    if method == "health.probe":
        oh_ok = await rpc.health()
        return {
            "gateway": {"ok": True, "detail": "shim alive"},
            "hostTools": {"ok": True, "detail": "host-side responsibility"},
            "network": {"ok": oh_ok, "detail": "openhuman-core /health"},
        }

    if method == "chat.send":
        message = str(params.get("message", ""))
        run = RunState(run_id=str(uuid.uuid4()))
        runs[run.run_id] = run
        run.task = asyncio.create_task(
            _execute_run(run, message, rpc, ws, subscribe_events)
        )
        return {"run_id": run.run_id}

    if method == "agent.wait":
        run_id = str(params.get("run_id") or params.get("runId") or "")
        run = runs.get(run_id)
        if run is None:
            raise ShimError(-32004, f"unknown run: {run_id}", retryable=False)
        timeout_s = float(params.get("timeout", 600))
        try:
            await asyncio.wait_for(run.done.wait(), timeout=min(timeout_s, 600.0))
        except TimeoutError:
            raise ShimError(-32001, "timeout waiting for run", retryable=True)
        if run.status == "error":
            raise ShimError(-32002, run.error or "agent error", retryable=False)
        return {"run_id": run_id, "status": "ok", "content": run.content}

    if method == "chat.history":
        # threads_messages_list's only input is thread_id (a `limit` param is
        # rejected with "unknown param"); it returns an envelope
        # {messages, count}. Limit shim-side, most recent last.
        limit = int(params.get("limit", 50))
        envelope = unwrap_rpc_result(
            await rpc.call(
                "openhuman.threads_messages_list",
                {"thread_id": THREAD_ID},
            )
        )
        msgs = (envelope or {}).get("messages") or []
        return {"messages": msgs[-limit:] if limit > 0 else msgs}

    raise OpenHumanRpcError(-32601, f"method not found: {method}")


def _http_process_request(
    connection: ServerConnection, request: Request
) -> Response | None:
    """Short-circuit Bridge's HTTP GET /health probe with a 200 OK before the
    websockets library tries to upgrade. Returning None falls through to the
    standard WS handshake for every other path.

    Bridge's HealthService probes `http://127.0.0.1:<hostPort>/health` (see
    `health_service.dart` _probePathBGateway). Without this, the GET reaches
    `websockets.serve` and fails with `InvalidUpgrade: missing Connection
    header`, leaving the G dot red even though the shim is alive.
    """
    if request.path == "/health":
        return connection.respond(200, "ok\n")
    return None


async def _serve_client(
    ws: ServerConnection,
    rpc: OpenHumanRpcClient,
    *,
    subscribe_events=None,
    runs: dict[str, RunState] | None = None,
) -> None:
    if runs is None:
        runs = _runs
    log.info("client connected: %s", ws.remote_address)
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as e:
                await ws.send(_error("", -32700, f"parse error: {e}"))
                continue
            req_id = str(msg.get("id", ""))
            method = str(msg.get("method", ""))
            params = msg.get("params") or {}
            try:
                result = await _dispatch(
                    method, params, rpc, ws, runs, subscribe_events
                )
                await ws.send(_response(req_id, result))
            except ShimError as e:
                await ws.send(_error(req_id, e.code, e.message, e.retryable))
            except OpenHumanRpcError as e:
                await ws.send(_error(req_id, e.code, e.message))
            except Exception as e:
                log.exception("dispatch failed for %s", method)
                await ws.send(_error(req_id, -32000, f"{type(e).__name__}: {e}"))
    except websockets.exceptions.ConnectionClosed:
        log.info("client disconnected")


async def serve_forever(host: str, port: int) -> None:
    from .event_stream import subscribe_events

    rpc = OpenHumanRpcClient()
    try:
        async with serve(
            lambda ws: _serve_client(ws, rpc, subscribe_events=subscribe_events),
            host,
            port,
            process_request=_http_process_request,
        ):
            log.info("shim listening on ws://%s:%s (HTTP /health enabled)", host, port)
            await asyncio.Future()  # run until cancelled
    finally:
        await rpc.aclose()
