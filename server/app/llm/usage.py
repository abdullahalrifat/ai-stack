"""Provider-neutral LLM usage instrumentation.

Pricing is configuration, never hard-coded into the agent loop. Set
LLM_PRICING_JSON to a JSON object mapping model ids to USD per 1M input/output
tokens. Providers that do not expose usage simply contribute latency/call
metrics from the existing instrumentation.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from .metrics import incr

_INSTALLED = False


def _pricing() -> dict[str, Any]:
    try:
        return json.loads(os.getenv("LLM_PRICING_JSON", "{}"))
    except json.JSONDecodeError:
        return {}


def _usage_value(usage: Any, *names: str) -> Any:
    """Read the first present token field from an object or mapping.

    Streaming paths (AS-5) and some providers deliver usage as a plain
    mapping, while others expose attributes; support both.
    """
    for name in names:
        value = (
            usage.get(name)
            if isinstance(usage, Mapping)
            else getattr(usage, name, None)
        )
        if value:
            return value
    return 0


def record_usage(model: str, usage: Any) -> dict[str, float]:
    prompt = int(_usage_value(usage, "prompt_tokens", "input_tokens"))
    completion = int(_usage_value(usage, "completion_tokens", "output_tokens"))
    total = int(_usage_value(usage, "total_tokens") or prompt + completion)
    prices = _pricing().get(model, {}) or {}
    input_rate = float(prices.get("input_usd_per_million", 0.0) or 0.0)
    output_rate = float(prices.get("output_usd_per_million", 0.0) or 0.0)
    cost = prompt * input_rate / 1_000_000 + completion * output_rate / 1_000_000
    incr(f"llm.tokens.{model}", total)
    incr(f"llm.requests.{model}")
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "estimated_cost_usd": cost,
    }


def install() -> None:
    """Wrap the OpenAI-compatible gateway once so usage is captured centrally."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import client

    original_get_client = client.get_client

    def wrapped_get_client():
        obj = original_get_client()
        marker = "_jarvis_usage_wrapped"
        if getattr(obj, marker, False):
            return obj
        original_create = obj.chat.completions.create

        def create(*args, **kwargs):
            response = original_create(*args, **kwargs)
            usage = getattr(response, "usage", None)
            if usage is not None:
                record_usage(str(kwargs.get("model") or "unknown"), usage)
            return response

        obj.chat.completions.create = create
        setattr(obj, marker, True)
        return obj

    client.get_client = wrapped_get_client
    _INSTALLED = True
