import sys
import types
import pytest
from hermes_codie_emitter import mcp_bridge


def _install_fake_mcp_tool(session_obj, run_impl):
    mod = types.ModuleType("tools.mcp_tool")
    mod._servers = {"codie_host": types.SimpleNamespace(session=session_obj)} if session_obj else {}
    mod._run_on_mcp_loop = run_impl
    tools_pkg = sys.modules.setdefault("tools", types.ModuleType("tools"))
    tools_pkg.mcp_tool = mod
    sys.modules["tools.mcp_tool"] = mod


def teardown_function():
    sys.modules.pop("tools.mcp_tool", None)
    if "tools" in sys.modules and hasattr(sys.modules["tools"], "mcp_tool"):
        delattr(sys.modules["tools"], "mcp_tool")


def test_no_codie_host_returns_none():
    _install_fake_mcp_tool(None, lambda coro, timeout=5: None)
    assert mcp_bridge.flush_batch("inst", [{"type": "chat_start", "local_seq": 1}]) is None


def test_dispatch_calls_tool_and_returns_last_local_seq():
    seen = {}
    class FakeSession:
        def call_tool(self, name, arguments):
            seen["name"] = name
            seen["args"] = arguments
            return "coro-sentinel"
    def fake_run(coro, timeout=5):
        assert coro == "coro-sentinel"
        return types.SimpleNamespace(
            structuredContent={"accepted": 1, "last_local_seq": 7}
        )
    _install_fake_mcp_tool(FakeSession(), fake_run)
    last = mcp_bridge.flush_batch("inst-7", [{"type": "chat_start", "local_seq": 7}])
    assert last == 7
    assert seen["name"] == "host_agent_event_report"
    assert seen["args"]["instance_id"] == "inst-7"
    # Bridge's EventChannelPublisher only broadcasts the "activity" category
    # to AgentDesk; "agent_event" (or anything else) is silently dropped.
    assert seen["args"]["category"] == "activity"
    assert seen["args"]["events"][0]["local_seq"] == 7


def test_unparseable_result_falls_back_to_batch_max_seq():
    class FakeSession:
        def call_tool(self, name, arguments):
            return "c"
    def weird(coro, timeout=5):
        return object()  # no .structuredContent, not a dict
    _install_fake_mcp_tool(FakeSession(), weird)
    last = mcp_bridge.flush_batch("i", [{"local_seq": 3}, {"local_seq": 9}])
    assert last == 9  # evict what we sent to guarantee progress


def test_call_error_propagates():
    class FakeSession:
        def call_tool(self, name, arguments):
            return "c"
    def boom(coro, timeout=5):
        raise RuntimeError("transport down")
    _install_fake_mcp_tool(FakeSession(), boom)
    with pytest.raises(RuntimeError):
        mcp_bridge.flush_batch("i", [{"local_seq": 1}])


def test_tool_error_result_returns_none_keeps_batch():
    class FakeSession:
        def call_tool(self, name, arguments):
            return "c"
    def err_result(coro, timeout=5):
        return types.SimpleNamespace(isError=True, structuredContent=None)
    _install_fake_mcp_tool(FakeSession(), err_result)
    # isError -> None means the emitter keeps the batch and retries (no silent drop)
    assert mcp_bridge.flush_batch("i", [{"local_seq": 5}]) is None
