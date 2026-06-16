import threading
from hermes_codie_emitter.ring import LocalRing


def test_append_stamps_monotonic_seq():
    r = LocalRing(maxlen=200)
    s1 = r.append({"type": "chat_start"})
    s2 = r.append({"type": "chat_end"})
    assert s2 == s1 + 1
    batch = r.peek(10)
    assert [e["type"] for e in batch] == ["chat_start", "chat_end"]
    assert [e["local_seq"] for e in batch] == [s1, s2]


def test_evict_through_is_seq_based_and_idempotent():
    r = LocalRing(maxlen=200)
    s1 = r.append({"n": 1}); r.append({"n": 2}); s3 = r.append({"n": 3})
    r.evict_through(s1)
    assert [e["n"] for e in r.peek(10)] == [2, 3]
    r.evict_through(s1)  # idempotent — already gone, no effect
    assert [e["n"] for e in r.peek(10)] == [2, 3]
    r.evict_through(s3)
    assert r.peek(10) == []


def test_overflow_drops_oldest():
    r = LocalRing(maxlen=2)
    r.append({"n": 1}); r.append({"n": 2}); r.append({"n": 3})
    assert [e["n"] for e in r.peek(10)] == [2, 3]


def test_concurrent_appends_assign_unique_seqs():
    r = LocalRing(maxlen=10000)
    seqs = []
    lock = threading.Lock()
    def worker():
        local = [r.append({}) for _ in range(500)]
        with lock:
            seqs.extend(local)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(seqs) == 8 * 500
    assert len(set(seqs)) == 8 * 500  # all unique — no lost increments under contention
