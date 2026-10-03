"""The Store is shared by the event loop and the threadpool (sync routes): it must survive concurrent use."""
import threading

from mxconsole import keys, metrics
from mxconsole.store import Store


def test_many_threads_reading_and_writing_at_once(tmp_path):
    store = Store(tmp_path / "mst.db")
    store.add_model(name="tiny", repo_id="org/tiny", revision="r", source="hf", architecture="X")
    key = keys.create_key(store, "app")
    errors, barrier = [], threading.Barrier(8)

    def worker(i):
        try:
            barrier.wait(timeout=10)
            for n in range(150):
                metrics.record(store, app="app", model="tiny", route="chat", status=200, prompt_tokens=1,
                               completion_tokens=5, ttft_ms=10.0, duration_ms=50.0, estimated=False)
                metrics.recent(store, 5)
                metrics.summary(store, window=300)
                keys.verify_key(store, key)
                store.models()
        except Exception as e:  # anything at all is a failure here
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert errors == []
    assert len(metrics.recent(store, 5000)) == 8 * 150
