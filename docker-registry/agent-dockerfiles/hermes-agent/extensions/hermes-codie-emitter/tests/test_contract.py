"""Contract test: every envelope the emitter produces must satisfy the sidecar's
AgentEventEntry required fields (type:str, run_id:str, ts_ms:int, data:dict,
local_seq:int) — run_id must NEVER be None/empty or the sidecar rejects the batch."""

import time
import types
from hermes_codie_emitter.emitter import CodieEmitter
import hermes_codie_emitter.shim as shim


def _assert_valid_envelope(env):
    assert isinstance(env["type"], str) and env["type"]
    assert isinstance(env["run_id"], str) and env["run_id"]      # never None/empty
    assert isinstance(env["ts_ms"], int)
    assert isinstance(env["data"], dict)
    assert isinstance(env["local_seq"], int)


def test_emitter_envelopes_satisfy_sidecar_contract():
    captured = []
    def dispatch(batch):
        captured.extend(batch)
        return batch[-1]["local_seq"]
    em = CodieEmitter(dispatch=dispatch, flush_interval=0.02)
    em.start()
    try:
        em.emit("chat_start", run_id="s1", data={})
        em.emit("tool_start", run_id=None, data={"name": "x"})   # None must be coerced
        deadline = time.monotonic() + 2
        while len(captured) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        em.stop()
    assert len(captured) == 2
    for env in captured:
        _assert_valid_envelope(env)


def test_tool_path_through_shim_produces_valid_run_id(monkeypatch):
    sent = []
    monkeypatch.setattr(shim, "_emit",
                        lambda et, rid, d: sent.append({"type": et, "run_id": rid,
                                                        "ts_ms": 1, "data": d, "local_seq": 1}))
    mod = types.ModuleType("run_agent")

    class AIAgent:
        def __init__(self):
            self.session_id = "sess-Z"
            self.tool_start_callback = None
            self.tool_complete_callback = None
        def run_conversation(self): return None
    mod.AIAgent = AIAgent
    shim._patch(mod)

    agent = mod.AIAgent()
    agent.tool_start_callback("tc", "host_open_url", {})
    agent.tool_complete_callback("tc", "host_open_url", {}, "ok")
    assert [e["run_id"] for e in sent] == ["sess-Z", "sess-Z"]
    for e in sent:
        _assert_valid_envelope(e)
