import logging
import os
import time

import requests
from openai import OpenAI

from .config import DEFAULT_MODEL, MODEL_LIST_CACHE_SECONDS

logger = logging.getLogger(__name__)

_client = None
_model_cache: dict = {"models": None, "fetched_at": 0.0}


def get_client():
    global _client

    if _client is None:
        _client = OpenAI(
            base_url=os.getenv("OPENAI_API_BASE"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    return _client


def get_available_models(force_refresh: bool = False):
    """Return the list of model ids the gateway currently serves.

    Cached for MODEL_LIST_CACHE_SECONDS so a normal chat/tool-call loop
    doesn't make an extra HTTP round trip to /models on every single step.
    """

    now = time.monotonic()

    if (
        not force_refresh
        and _model_cache["models"] is not None
        and now - _model_cache["fetched_at"] < MODEL_LIST_CACHE_SECONDS
    ):
        return _model_cache["models"]

    base_url = os.getenv("OPENAI_API_BASE", "http://litellm:4000/v1")
    api_key = os.getenv("OPENAI_API_KEY")

    response = requests.get(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()
    models = [item["id"] for item in data["data"]]

    _model_cache["models"] = models
    _model_cache["fetched_at"] = now

    return models


def _ensure_model_available(model: str):
    available = get_available_models()

    if model not in available:
        # The model may have been registered after our cache was populated;
        # refresh once before giving up.
        available = get_available_models(force_refresh=True)

        if model not in available:
            raise ValueError(f"Model '{model}' not available. Available: {available}")


def chat(messages, model=DEFAULT_MODEL) -> str:
    """Plain-text completion, no tool calling. Used by the planner and by
    internal helpers like context compaction."""

    _ensure_model_available(model)
    client = get_client()

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        timeout=120,
    )

    return response.choices[0].message.content


def chat_with_tools(messages, tools, model=DEFAULT_MODEL, tool_choice="auto"):
    """Completion using native function calling.

    Returns the raw response message object, exposing both `.content` and
    `.tool_calls` so callers can branch on whichever the model produced,
    instead of asking the model to hand-write JSON tool calls in prose.
    """

    _ensure_model_available(model)
    client = get_client()

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=0,
        timeout=120,
    )

    return response.choices[0].message