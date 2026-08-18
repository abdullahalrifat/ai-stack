"""Simple thread-safe LRU cache for LLM responses.

This is intentionally lightweight and kept inside the agents package so tests
can exercise caching behavior without external dependencies.
"""
from collections import OrderedDict
import threading
import json
import os
from types import SimpleNamespace
from typing import Any


class LRUCache:
    def __init__(self, max_size: int = 256):
        self.max_size = max_size
        self.lock = threading.Lock()
        self.data: "OrderedDict[str, Any]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _ensure_limit(self):
        while len(self.data) > self.max_size:
            self.data.popitem(last=False)

    def make_key(self, *parts) -> str:
        try:
            return json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            # Fallback to repr when parts contain non-serializable objects
            return repr(parts)

    def get(self, key: str):
        with self.lock:
            if key in self.data:
                value = self.data.pop(key)
                # move to end (most-recent)
                self.data[key] = value
                self.hits += 1
                return value
            self.misses += 1
            return None

    def set(self, key: str, value: Any):
        with self.lock:
            if key in self.data:
                self.data.pop(key)
            self.data[key] = value
            self._ensure_limit()

    def stats(self):
        with self.lock:
            return {"size": len(self.data), "hits": self.hits, "misses": self.misses}


# Module-level default cache used by the client. Size adjustable via env
try:
    _env_size = int(os.getenv("LLM_CACHE_SIZE", "256"))
except Exception:
    _env_size = 256

default_cache = LRUCache(max_size=_env_size)


def msg_key(messages, model, max_tokens):
    return default_cache.make_key(messages, model, max_tokens)


def make_message_like(obj: dict):
    """Return a simple namespace with `.content` and optional `.tool_calls` to
    mimic the response message objects the client returns.
    """
    ns = SimpleNamespace()
    ns.content = obj.get("content")
    ns.tool_calls = obj.get("tool_calls")
    return ns
