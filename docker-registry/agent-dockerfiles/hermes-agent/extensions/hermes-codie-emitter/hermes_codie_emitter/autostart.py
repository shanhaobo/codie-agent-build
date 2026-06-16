"""`.pth` entrypoint. Activates the Codie emitter only inside Codie containers
(gated on CODIE_INSTANCE_ID). Patches run_agent.AIAgent immediately if loaded,
otherwise installs a one-shot post-import hook (no wrapt dependency)."""

import importlib.abc
import importlib.util
import os
import sys

from . import shim
from ._log import dlog


class _RunAgentPatchFinder(importlib.abc.MetaPathFinder):
    """Wraps run_agent's loader.exec_module to patch AIAgent right after the
    module body executes (class defined), then removes itself."""

    def find_spec(self, fullname, path, target=None):
        if fullname != "run_agent":
            return None
        # Strictly one-shot: remove ourselves before resolving the real spec so
        # the find_spec() below doesn't recurse into us. We never re-arm — in the
        # container run_agent is always importable, so the first import is the
        # one we patch; if find_spec yielded no spec we'd simply be gone (a case
        # that can't occur for a module the gateway imports unconditionally).
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        spec = importlib.util.find_spec("run_agent")
        if spec is None or spec.loader is None:
            return None
        orig_exec = spec.loader.exec_module

        def exec_module(module):
            orig_exec(module)
            try:
                dlog("post-import hook fired: patching run_agent")
                shim._patch(module)
                shim._installed = True
            except Exception as e:
                dlog("post-import patch FAILED: %r" % (e,))

        spec.loader.exec_module = exec_module  # type: ignore[assignment]
        return spec


def activate() -> bool:
    """Return True if patching happened or a deferred hook was installed."""
    if not os.environ.get("CODIE_INSTANCE_ID"):
        return False
    if "run_agent" in sys.modules:
        dlog("activate(): run_agent already imported → install() now")
        return shim.install()
    if not any(isinstance(f, _RunAgentPatchFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _RunAgentPatchFinder())
        dlog("activate(): armed deferred post-import finder for run_agent")
    return True


# Executed at interpreter startup via the .pth line `import hermes_codie_emitter.autostart`.
try:
    activate()
except Exception:
    pass
