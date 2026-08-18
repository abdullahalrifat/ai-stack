"""Lightweight in-memory metrics and timing helpers for LLM instrumentation.

This avoids external dependencies and gives a place to aggregate simple
observability signals (counts, latencies, cache/scheduler snapshots).
"""
import threading
import time
from typing import Callable, Any

_lock = threading.Lock()
_counters: dict[str, int] = {}
_timings: dict[str, list[float]] = {}


def incr(name: str, n: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + n


def record_timing(name: str, value: float) -> None:
    with _lock:
        _timings.setdefault(name, []).append(value)


def timing(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                record_timing(name, elapsed)

        return wrapped

    return deco


def get_metrics():
    with _lock:
        # Provide simple aggregates
        avg = {k: (sum(v) / len(v) if v else 0.0) for k, v in _timings.items()}
        return {"counters": dict(_counters), "avg_timings": avg}
