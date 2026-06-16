"""Runtime shim: patches run_agent.AIAgent to emit boundary events.

Tasks 6-7 add the actual patching; this module owns the singleton emitter and
the emit helpers the patched methods call."""

import os
import threading
from typing import Any, Optional

from .emitter import CodieEmitter
from ._log import dlog

_emitter: Optional[CodieEmitter] = None
_emitter_lock = threading.Lock()
_installed = False
_install_lock = threading.Lock()


def _get_emitter() -> CodieEmitter:
    global _emitter
    if _emitter is None:
        with _emitter_lock:
            if _emitter is None:
                from . import mcp_bridge
                instance_id = os.environ.get("CODIE_INSTANCE_ID", "")
                # CodieEmitter takes no instance_id — the dispatch closure owns it.
                em = CodieEmitter(
                    dispatch=lambda batch: mcp_bridge.flush_batch(instance_id, batch),
                )
                em.start()
                _emitter = em
    return _emitter


def _emit(event_type: str, run_id: Any, data: dict) -> None:
    try:
        dlog("_emit %s run_id=%r" % (event_type, run_id))
        _get_emitter().emit(event_type, run_id, data)
    except Exception as e:
        dlog("_emit %s FAILED: %r" % (event_type, e))


def _is_subagent(agent: Any) -> bool:
    return getattr(agent, "_subagent_id", None) is not None


def _emit_run_start(agent: Any) -> None:
    if _is_subagent(agent):
        _emit("subagent_spawn", getattr(agent, "session_id", None), {
            "subagent_id": getattr(agent, "_subagent_id", None),
            "parent_subagent_id": getattr(agent, "_parent_subagent_id", None),
            "goal": getattr(agent, "_subagent_goal", None),
            "depth": getattr(agent, "_delegate_depth", None),
        })
    else:
        _emit("chat_start", getattr(agent, "session_id", None), {"error": False})


def _emit_run_end(agent: Any, error: Optional[BaseException]) -> None:
    data = {"error": error is not None}
    if _is_subagent(agent):
        data["subagent_id"] = getattr(agent, "_subagent_id", None)
        _emit("subagent_end", getattr(agent, "session_id", None), data)
    else:
        _emit("chat_end", getattr(agent, "session_id", None), data)


_PATCH_MARKER = "_codie_emitter_patched"


def _wrap_run_conversation(orig):
    def run_conversation(self, *args, **kwargs):
        dlog("run_conversation() ENTER subagent=%s" % (_is_subagent(self),))
        try:
            _emit_run_start(self)
        except Exception:
            pass
        err = None
        try:
            return orig(self, *args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — re-raised below
            err = e
            raise
        finally:
            try:
                _emit_run_end(self, err)
            except Exception:
                pass

    run_conversation._codie_orig = orig  # type: ignore[attr-defined]
    return run_conversation


def _wrap_init(orig):
    def __init__(self, *args, **kwargs):
        orig(self, *args, **kwargs)
        try:
            self.tool_start_callback = _chain_tool_start(
                getattr(self, "tool_start_callback", None), self
            )
            self.tool_complete_callback = _chain_tool_complete(
                getattr(self, "tool_complete_callback", None), self
            )
            dlog("__init__ wrapped: tool callbacks chained (session_id=%r)"
                 % (getattr(self, "session_id", "<none>"),))
        except Exception as e:
            dlog("__init__ chain FAILED: %r" % (e,))

    __init__._codie_orig = orig  # type: ignore[attr-defined]
    return __init__


def _chain_tool_start(prev, agent):
    def cb(tool_call_id=None, name=None, args=None, *extra, **kw):
        try:
            _emit("tool_start", getattr(agent, "session_id", None),
                  {"tool_call_id": tool_call_id, "name": name})
        except Exception:
            pass
        if prev is not None:
            return prev(tool_call_id, name, args, *extra, **kw)
    return cb


def _chain_tool_complete(prev, agent):
    def cb(tool_call_id=None, name=None, args=None, result=None, *extra, **kw):
        try:
            _emit("tool_end", getattr(agent, "session_id", None),
                  {"tool_call_id": tool_call_id, "name": name})
        except Exception:
            pass
        if prev is not None:
            return prev(tool_call_id, name, args, result, *extra, **kw)
    return cb


def _patch(run_agent_module) -> None:
    AIAgent = getattr(run_agent_module, "AIAgent", None)
    if AIAgent is None or getattr(AIAgent, _PATCH_MARKER, False):
        return
    AIAgent.run_conversation = _wrap_run_conversation(AIAgent.run_conversation)
    AIAgent.__init__ = _wrap_init(AIAgent.__init__)
    setattr(AIAgent, _PATCH_MARKER, True)
    dlog("patched AIAgent.run_conversation + __init__")


def install() -> bool:
    """Patch run_agent.AIAgent if importable. Idempotent. Returns success."""
    global _installed
    with _install_lock:
        if _installed:
            return True
        import sys
        mod = sys.modules.get("run_agent")
        if mod is None:
            try:
                import run_agent as mod  # type: ignore
            except Exception:
                return False
        _patch(mod)
        _installed = True
        return True
