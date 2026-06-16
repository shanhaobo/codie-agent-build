import sys
import types
import importlib
import hermes_codie_emitter.shim as shim


def _reset_shim():
    shim._installed = False
    shim._emitter = None


def test_no_instance_id_is_noop(monkeypatch):
    monkeypatch.delenv("CODIE_INSTANCE_ID", raising=False)
    _reset_shim()
    import hermes_codie_emitter.autostart as autostart
    importlib.reload(autostart)
    assert autostart.activate() is False
    assert shim._installed is False


def test_patches_immediately_when_run_agent_present(monkeypatch):
    monkeypatch.setenv("CODIE_INSTANCE_ID", "inst-X")
    _reset_shim()
    mod = types.ModuleType("run_agent")

    class AIAgent:
        def __init__(self): self.session_id = "s"
        def run_conversation(self): return 1
    mod.AIAgent = AIAgent
    monkeypatch.setitem(sys.modules, "run_agent", mod)

    import hermes_codie_emitter.autostart as autostart
    importlib.reload(autostart)
    assert autostart.activate() is True
    assert getattr(mod.AIAgent, shim._PATCH_MARKER, False) is True


def test_deferred_patch_via_meta_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CODIE_INSTANCE_ID", "inst-Y")
    _reset_shim()
    sys.modules.pop("run_agent", None)
    p = tmp_path / "run_agent.py"
    p.write_text(
        "class AIAgent:\n"
        "    def __init__(self): self.session_id='s'\n"
        "    def run_conversation(self): return 1\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    import hermes_codie_emitter.autostart as autostart
    importlib.reload(autostart)
    autostart.activate()  # run_agent not imported yet -> installs finder

    import run_agent  # triggers the finder -> _patch runs post-load
    assert getattr(run_agent.AIAgent, shim._PATCH_MARKER, False) is True
    sys.modules.pop("run_agent", None)
