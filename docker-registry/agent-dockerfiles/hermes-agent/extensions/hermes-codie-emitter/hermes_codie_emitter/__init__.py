"""Codie boundary-events emitter for Hermes (runtime shim).

Public API: ``install()`` patches ``run_agent.AIAgent`` to emit boundary events.
Normally invoked indirectly by ``autostart`` (the .pth entrypoint); exposed here
for tests and manual wiring.
"""

__all__ = ["install"]


def install() -> bool:
    """Idempotently patch run_agent.AIAgent. Returns True if patching happened
    (or was already done), False if run_agent is unavailable."""
    from . import shim
    return shim.install()
