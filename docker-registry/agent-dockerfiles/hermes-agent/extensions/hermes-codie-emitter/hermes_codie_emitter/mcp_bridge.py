"""Dispatch boundary-event batches to codie-host-mcp by reusing Hermes' own
in-process MCP background loop. All Hermes imports are lazy so this package
imports cleanly without Hermes installed.

Returns the highest local_seq the sidecar acked (last_local_seq), which the
emitter uses for seq-based eviction. The sidecar's AgentEventEntry requires a
``local_seq`` on every event (it returns last_local_seq = max(local_seq))."""

from typing import Any, Dict, List, Optional

from ._log import dlog

_FLUSH_TIMEOUT = 5  # seconds; matches the agentcore emitter's flush budget


def _extract_last_seq(result: Any, batch: List[Dict[str, Any]]) -> Optional[int]:
    """Pull last_local_seq out of an MCP CallToolResult, defensively.

    The sidecar returns {"accepted": int, "last_local_seq": int}; mcp surfaces it
    under .structuredContent (mcp>=1.26). If the tool reported an error, return
    None so the batch is KEPT and retried (do not evict undelivered events). If
    the call succeeded but the shape is unexpected, fall back to the max local_seq
    we sent — evicting what we sent guarantees forward progress."""
    if getattr(result, "isError", False):
        return None  # sidecar rejected/errored -> keep batch, retry next tick
    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict) and isinstance(sc.get("last_local_seq"), int):
        return sc["last_local_seq"]
    if isinstance(result, dict) and isinstance(result.get("last_local_seq"), int):
        return result["last_local_seq"]
    seqs = [e.get("local_seq", 0) for e in batch]
    return max(seqs) if seqs else None


def flush_batch(instance_id: str, batch: List[Dict[str, Any]]) -> Optional[int]:
    """Send a batch; return last acked local_seq, or None if codie_host is not
    ready (caller keeps the batch for the next tick)."""
    try:
        from tools.mcp_tool import _servers, _run_on_mcp_loop  # lazy
    except Exception as e:
        dlog("flush_batch: import tools.mcp_tool FAILED: %r" % (e,))
        return None

    server = _servers.get("codie_host")
    session = getattr(server, "session", None) if server else None
    dlog("flush_batch: servers=%r codie_host=%s session=%s"
         % (list(_servers.keys()), "yes" if server else "no",
            "yes" if session is not None else "no"))
    if session is None:
        return None  # not connected yet — caller keeps the batch

    try:
        result = _run_on_mcp_loop(
            session.call_tool(
                "host_agent_event_report",
                {
                    # MUST be "activity": Bridge's EventChannelPublisher only
                    # broadcasts the "activity" category to AgentDesk; anything
                    # else enters the ring but is silently never pushed. The
                    # openclaw + agentcore emitters both use "activity".
                    "category": "activity",
                    "instance_id": instance_id,
                    "events": batch,
                },
            ),
            timeout=_FLUSH_TIMEOUT,
        )
    except Exception as e:
        dlog("flush_batch: call_tool raised: %r" % (e,))
        raise
    dlog("flush_batch: result isError=%r" % (getattr(result, "isError", "<n/a>"),))
    return _extract_last_seq(result, batch)
