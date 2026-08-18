"""Benchmark cache + scheduler behavior with simulated LLM calls.

Run with: python3 scripts/llm_benchmark.py
"""
import time
import threading
import runpy

# Load modules without importing app.llm to avoid external deps
cache_mod = runpy.run_path('server/app/llm/cache.py')
scheduler_mod = runpy.run_path('server/app/llm/scheduler.py')

default_cache = cache_mod['default_cache']
def make_key(x):
    return default_cache.make_key(x, 'model-x', 128)

default_scheduler = scheduler_mod['default_scheduler']

# Counter for actual "model" calls
call_count = 0
call_count_lock = threading.Lock()


def faux_model_call(payload):
    global call_count
    with call_count_lock:
        call_count += 1
    # simulate work
    time.sleep(0.05)
    return f"resp:{payload}"


def worker(payload, use_cache=True):
    key = make_key(payload)
    if use_cache:
        cached = default_cache.get(key)
        if cached is not None:
            return cached
    # Use scheduler coalescing key to dedupe in-flight identical work
    def do():
        return faux_model_call(payload)
    res = default_scheduler.submit_sync(do, key=key)
    if use_cache:
        default_cache.set(key, res)
    return res


def run_round(n_threads=20, payloads=None, use_cache=True):
    global call_count
    call_count = 0
    if payloads is None:
        payloads = ["A"] * n_threads
    results = []
    threads = []
    start = time.time()
    for p in payloads:
        t = threading.Thread(target=lambda q, arg: q.append(worker(arg, use_cache=use_cache)), args=(results, p))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = time.time() - start
    return duration, call_count, results


if __name__ == '__main__':
    print('Cache initial stats:', default_cache.stats())
    # Round 1: all identical, cache disabled -> expect coalescing to reduce model calls
    d, c, _ = run_round(n_threads=20, payloads=['X']*20, use_cache=False)
    print(f'No-cache coalesced: duration={d:.3f}s, model_calls={c}')

    # Round 2: cache enabled but empty -> first run populates cache
    d, c, _ = run_round(n_threads=20, payloads=['Y']*20, use_cache=True)
    print(f'With-cache first run: duration={d:.3f}s, model_calls={c}, cache stats={default_cache.stats()}')

    # Round 3: cache enabled, repeated payloads -> should hit cache and zero model calls
    d, c, _ = run_round(n_threads=20, payloads=['Y']*20, use_cache=True)
    print(f'With-cache second run: duration={d:.3f}s, model_calls={c}, cache stats={default_cache.stats()}')

    # Round 4: mixed payloads -> some unique, some duplicates
    payloads = ['A']*10 + ['B']*5 + ['C']*5
    d, c, _ = run_round(n_threads=len(payloads), payloads=payloads, use_cache=True)
    print(f'Mixed payloads: duration={d:.3f}s, model_calls={c}, cache stats={default_cache.stats()}')
