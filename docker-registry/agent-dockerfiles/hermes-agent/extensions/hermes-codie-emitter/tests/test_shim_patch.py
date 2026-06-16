import types
import hermes_codie_emitter.shim as shim


def _make_fake_run_agent():
    mod = types.ModuleType("run_agent")

    class AIAgent:
        def __init__(self, tool_start_callback=None, tool_complete_callback=None):
            self.session_id = "sess-A"
            self.tool_start_callback = tool_start_callback
            self.tool_complete_callback = tool_complete_callback
            self.ran = False

        def run_conversation(self, msg):
            self.ran = True
            return f"reply:{msg}"

    mod.AIAgent = AIAgent
    return mod


def test_run_conversation_emits_chat_boundaries(monkeypatch):
    events = []
    monkeypatch.setattr(shim, "_emit", lambda et, rid, d: events.append(et))
    mod = _make_fake_run_agent()
    shim._patch(mod)

    agent = mod.AIAgent()
    assert agent.run_conversation("hi") == "reply:hi"
    assert agent.ran is True
    assert events == ["chat_start", "chat_end"]


def test_run_conversation_emits_chat_end_on_error(monkeypatch):
    events = []
    monkeypatch.setattr(shim, "_emit", lambda et, rid, d: events.append((et, d.get("error"))))
    mod = _make_fake_run_agent()

    def boom(self, msg):
        raise ValueError("nope")
    mod.AIAgent.run_conversation = boom
    shim._patch(mod)

    agent = mod.AIAgent()
    try:
        agent.run_conversation("x")
    except ValueError:
        pass
    assert events[0] == ("chat_start", False)
    assert events[1] == ("chat_end", True)


def test_tool_callbacks_chained_and_emit(monkeypatch):
    events = []
    monkeypatch.setattr(shim, "_emit", lambda et, rid, d: events.append((et, rid, d)))
    mod = _make_fake_run_agent()
    shim._patch(mod)

    prev_calls = []
    agent = mod.AIAgent(
        tool_start_callback=lambda *a: prev_calls.append(("start", a)),
        tool_complete_callback=lambda *a: prev_calls.append(("complete", a)),
    )
    agent.tool_start_callback("tc-1", "host_open_url", {"url": "x"})
    agent.tool_complete_callback("tc-1", "host_open_url", {"url": "x"}, "ok")

    assert prev_calls == [
        ("start", ("tc-1", "host_open_url", {"url": "x"})),
        ("complete", ("tc-1", "host_open_url", {"url": "x"}, "ok")),
    ]
    # tool events MUST carry the agent's session_id as run_id (not None) —
    # the sidecar rejects run_id=None and would drop the whole batch.
    assert events[0][0] == "tool_start" and events[0][1] == "sess-A"
    assert events[0][2]["name"] == "host_open_url" and events[0][2]["tool_call_id"] == "tc-1"
    assert events[1][0] == "tool_end" and events[1][1] == "sess-A"
    assert events[1][2]["name"] == "host_open_url"


def test_tool_callbacks_work_when_none_preset(monkeypatch):
    events = []
    monkeypatch.setattr(shim, "_emit", lambda et, rid, d: events.append((et, rid)))
    mod = _make_fake_run_agent()
    shim._patch(mod)
    agent = mod.AIAgent()  # no callbacks preset
    agent.tool_start_callback("tc", "t", {})       # must not raise
    agent.tool_complete_callback("tc", "t", {}, "r")
    assert events == [("tool_start", "sess-A"), ("tool_end", "sess-A")]


def test_patch_is_idempotent(monkeypatch):
    monkeypatch.setattr(shim, "_emit", lambda *a: None)
    mod = _make_fake_run_agent()
    shim._patch(mod)
    first = mod.AIAgent.run_conversation
    shim._patch(mod)
    assert mod.AIAgent.run_conversation is first  # not double-wrapped
