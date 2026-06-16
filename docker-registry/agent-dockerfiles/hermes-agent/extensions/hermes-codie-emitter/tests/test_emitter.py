import time
from hermes_codie_emitter.emitter import CodieEmitter


def test_emit_builds_envelope_with_local_seq_and_dispatches():
    got = []
    def dispatch(batch):
        got.extend(batch)
        return batch[-1]["local_seq"]  # ack the highest seq in the batch
    em = CodieEmitter(dispatch=dispatch, flush_interval=0.05)
    em.start()
    try:
        em.emit("chat_start", run_id="sess-9", data={"k": "v"})
        deadline = time.monotonic() + 2
        while not got and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        em.stop()
    assert len(got) == 1
    env = got[0]
    assert env["type"] == "chat_start"
    assert env["run_id"] == "sess-9"
    assert env["data"] == {"k": "v"}
    assert isinstance(env["ts_ms"], int) and env["ts_ms"] > 0
    assert isinstance(env["local_seq"], int) and env["local_seq"] > 0


def test_event_survives_until_acked():
    # dispatch returns None (not connected) once, then acks. The same event must
    # be re-presented (not evicted) until a real ack comes back.
    calls = {"n": 0}
    seen_sizes = []
    def dispatch(batch):
        calls["n"] += 1
        seen_sizes.append(len(batch))
        if calls["n"] == 1:
            return None  # not connected; no eviction
        return batch[-1]["local_seq"]
    em = CodieEmitter(dispatch=dispatch, flush_interval=0.05)
    em.start()
    try:
        em.emit("tool_start", run_id="s", data={})
        deadline = time.monotonic() + 2
        while calls["n"] < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        em.stop()
    assert calls["n"] >= 2
    assert seen_sizes[0] == 1 and seen_sizes[1] == 1  # same event re-presented


def test_failed_dispatch_keeps_events_for_retry():
    calls = {"n": 0}
    def flaky(batch):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transport down")
        return batch[-1]["local_seq"]
    em = CodieEmitter(dispatch=flaky, flush_interval=0.05)
    em.start()
    try:
        em.emit("tool_start", run_id="s", data={})
        deadline = time.monotonic() + 2
        while calls["n"] < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        em.stop()
    assert calls["n"] >= 2  # retried after the raise; event not lost


def test_emit_never_raises_when_dispatch_broken():
    em = CodieEmitter(dispatch=None, flush_interval=0.05)
    em.start()
    try:
        em.emit("chat_end", run_id="s", data={})  # must not raise
    finally:
        em.stop()


def test_emit_coerces_none_run_id_to_nonempty_string():
    got = []
    def dispatch(batch):
        got.extend(batch)
        return batch[-1]["local_seq"]
    em = CodieEmitter(dispatch=dispatch, flush_interval=0.05)
    em.start()
    try:
        em.emit("tool_start", run_id=None, data={})
        deadline = time.monotonic() + 2
        while not got and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        em.stop()
    assert len(got) == 1
    assert isinstance(got[0]["run_id"], str) and got[0]["run_id"]  # non-empty str, never None
