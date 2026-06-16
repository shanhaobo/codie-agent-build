"""Lightweight stderr diagnostic logger for the emitter.

Gated on CODIE_EMITTER_DEBUG (off by default) so production containers stay
quiet. Set CODIE_EMITTER_DEBUG=1 in the container env to trace the emit/flush
pipeline on stderr (visible via `docker logs`).

This is intentionally dependency-free and swallows its own errors: logging must
never perturb the agent loop."""

import os
import sys
import threading

_ENABLED = bool(os.environ.get("CODIE_EMITTER_DEBUG"))
_lock = threading.Lock()


def dlog(msg: str) -> None:
    if not _ENABLED:
        return
    try:
        with _lock:
            sys.stderr.write("[codie-emitter] " + msg + "\n")
            sys.stderr.flush()
    except Exception:
        pass
