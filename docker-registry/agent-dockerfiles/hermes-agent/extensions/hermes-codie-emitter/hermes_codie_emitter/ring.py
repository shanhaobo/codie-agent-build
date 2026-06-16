"""Thread-safe bounded ring buffer for boundary-event envelopes.

Each appended envelope is stamped with a monotonic ``local_seq`` so eviction is
seq-based (idempotent) rather than count-based: the sidecar acks the highest
local_seq it accepted, and we evict everything <= that seq. This mirrors the
shipped openclaw events-emitter (local-ring.ts peekFrom/evictThrough) and is
immune to the overflow-during-dispatch race that count-based eviction has. The
sidecar also REQUIRES local_seq on every event (models.py AgentEventEntry)."""

import threading
from collections import deque
from typing import Any, Dict, List


class LocalRing:
    def __init__(self, maxlen: int = 200):
        self._dq: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._next_seq = 1  # local_seq starts at 1; 0 means "nothing acked yet"

    def append(self, envelope: Dict[str, Any]) -> int:
        """Stamp the envelope with a monotonic local_seq, store it, return the
        seq. deque(maxlen) drops the oldest entry on overflow."""
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            envelope["local_seq"] = seq
            self._dq.append(envelope)
            return seq

    def peek(self, n: int) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for i, env in enumerate(self._dq):
                if i >= n:
                    break
                out.append(env)
            return out

    def evict_through(self, last_seq: int) -> None:
        """Remove all entries with local_seq <= last_seq (idempotent)."""
        with self._lock:
            while self._dq and self._dq[0].get("local_seq", 0) <= last_seq:
                self._dq.popleft()

    def __len__(self) -> int:
        with self._lock:
            return len(self._dq)
