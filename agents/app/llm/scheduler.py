"""Simple request queue / worker pool for LLM calls.

This serializes and pools LLM requests to provide a stable concurrency
envelope and to optionally add batching or coalescing later.
"""
import threading
import queue
from typing import Callable, Any


class _WorkItem:
    def __init__(self, fn: Callable, args: tuple, kwargs: dict, key: str | None = None):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.key = key
        self.event = threading.Event()
        self.result = None
        self.exc = None


class LLMScheduler:
    def __init__(self, workers: int = 1):
        self.workers = workers
        self.q = queue.Queue()
        self.lock = threading.Lock()
        # Map from coalescing key -> list[_WorkItem]
        self.in_flight: dict[str, list[_WorkItem]] = {}
        self.threads = []
        self._start_workers()

    def _start_workers(self):
        for i in range(self.workers):
            t = threading.Thread(target=self._worker, daemon=True, name=f"llm-sched-{i}")
            t.start()
            self.threads.append(t)

    def _worker(self):
        while True:
            item: _WorkItem = self.q.get()
            try:
                res = item.fn(*item.args, **item.kwargs)
                item.result = res
            except Exception as exc:  # noqa: BLE001 - allow broad capture for worker
                item.exc = exc
            finally:
                # If this item had a coalescing key, propagate the result to
                # all listeners that were waiting for the same key.
                if item.key is not None:
                    with self.lock:
                        listeners = self.in_flight.pop(item.key, [])
                    for listener in listeners:
                        listener.result = item.result
                        listener.exc = item.exc
                        listener.event.set()
                else:
                    item.event.set()
                self.q.task_done()

    def submit_sync(self, fn: Callable, *args, key: str | None = None, **kwargs) -> Any:
        item = _WorkItem(fn, args, kwargs, key=key)
        if key is not None:
            with self.lock:
                if key in self.in_flight:
                    # Another worker is already processing this key; append
                    # ourselves to the listeners.
                    self.in_flight[key].append(item)
                else:
                    # Register as the first listener and queue the work.
                    self.in_flight[key] = [item]
                    self.q.put(item)

            item.event.wait()
            if item.exc:
                raise item.exc
            return item.result

        # No coalescing key provided; just queue a standalone work item.
        self.q.put(item)
        item.event.wait()
        if item.exc:
            raise item.exc
        return item.result


# Module-level default scheduler. Worker count configurable via env when
# constructing callers choose to adapt.
default_scheduler = LLMScheduler(workers=1)


def get_scheduler_stats():
    """Return basic scheduler stats for observability/tests."""
    return {
        "workers": default_scheduler.workers,
        "queue_size": default_scheduler.q.qsize(),
        "in_flight_keys": list(default_scheduler.in_flight.keys()),
    }
