"""Boundary-event emitter: builds envelopes, rings them, drains on a daemon thread."""

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .ring import LocalRing
from ._log import dlog

BATCH_SIZE = 50


class CodieEmitter:
    def __init__(
        self,
        dispatch: Optional[Callable[[List[Dict[str, Any]]], Optional[int]]],
        ring_maxlen: int = 200,
        batch_size: int = BATCH_SIZE,
        flush_interval: float = 1.0,
    ):
        # dispatch(batch) -> last acked local_seq (int), or None to keep the
        # batch for the next tick (e.g. sidecar not connected yet).
        self._dispatch = dispatch
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._ring = LocalRing(maxlen=ring_maxlen)
        self._cond = threading.Condition()
        self._stopped = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="codie-emitter-flush", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
        t = self._thread
        if t is not None:
            t.join(timeout=2)

    def emit(self, event_type: str, run_id: Any, data: Dict[str, Any]) -> None:
        # VOID + best-effort: never raise into the agent loop.
        try:
            # The sidecar's AgentEventEntry requires run_id: str. Coerce None/empty
            # to a placeholder so one missing id can't reject (and silently drop)
            # the whole batch.
            rid = run_id if isinstance(run_id, str) and run_id else "unknown"
            env = {
                "type": event_type,
                "run_id": rid,
                "ts_ms": int(time.time() * 1000),
                "data": data or {},
            }
            self._ring.append(env)  # stamps local_seq under the ring lock
            with self._cond:
                self._cond.notify_all()
        except Exception:
            pass

    def _run(self) -> None:
        while True:
            with self._cond:
                if not self._stopped and len(self._ring) == 0:
                    self._cond.wait(timeout=self._flush_interval)
                if self._stopped and len(self._ring) == 0:
                    return
            try:
                self._flush_once()
            except Exception as e:
                # Top-level catch-all: a single raise must not kill the thread
                # (agentcore lesson, 2026-05-28). Swallow and loop; the batch is
                # left un-evicted and retried next tick.
                dlog("_flush_once raised (batch kept): %r" % (e,))

    def _flush_once(self) -> None:
        batch = self._ring.peek(self._batch_size)
        if not batch:
            return
        if self._dispatch is None:
            return  # nothing to send to; leave batch for a real dispatch
        dlog("flush_once: dispatching batch=%d (ring=%d)" % (len(batch), len(self._ring)))
        last_seq = self._dispatch(batch)  # may raise -> caught by _run, batch kept
        dlog("flush_once: dispatch returned last_seq=%r" % (last_seq,))
        if isinstance(last_seq, int) and last_seq > 0:
            self._ring.evict_through(last_seq)
