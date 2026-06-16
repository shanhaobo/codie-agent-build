import hermes_codie_emitter.shim as shim


def test_emit_helpers_route_through_emitter(monkeypatch):
    captured = []
    monkeypatch.setattr(shim, "_emit", lambda et, rid, data: captured.append((et, rid, data)))

    class FakeAgent:
        session_id = "sess-1"

    class FakeSub:
        session_id = "sess-2"
        _subagent_id = "sa-9"
        _parent_subagent_id = "sa-root"
        _subagent_goal = "do the thing"
        _delegate_depth = 2

    shim._emit_run_start(FakeAgent())
    shim._emit_run_end(FakeAgent(), error=None)
    shim._emit_run_start(FakeSub())
    shim._emit_run_end(FakeSub(), error=RuntimeError("x"))

    assert captured[0][0] == "chat_start" and captured[0][1] == "sess-1"
    assert captured[1][0] == "chat_end"
    assert captured[2][0] == "subagent_spawn" and captured[2][1] == "sess-2"
    assert captured[2][2]["subagent_id"] == "sa-9"
    assert captured[2][2]["goal"] == "do the thing"
    assert captured[2][2]["depth"] == 2
    assert captured[3][0] == "subagent_end"
    assert captured[3][2]["error"] is True
