import logging
import os
import time

import requests
from openai import OpenAI

from ..core.config import (
    AGENT_MODEL_ID,
    DEFAULT_MODEL,
    LLM_MAX_COMPLETION_TOKENS,
    LLM_TIMEOUT_SECONDS,
    MODEL_LIST_CACHE_SECONDS,
)

logger = logging.getLogger(__name__)

_client = None
_model_cache: dict = {"models": None, "fetched_at": 0.0}


def get_client():
    
    global _client

    if _client is None:
        _client = OpenAI(
            base_url=os.getenv("OPENAI_API_BASE"),
            api_key=os.getenv("OPENAI_API_KEY"),
            # Retrying a timed-out local inference request duplicates work on
            # Ollama's single queue, making every subsequent response slower.
            max_retries=0,
        )

    return _client


def resolve_agent_model(model: str | None):

    if not model:
        return DEFAULT_MODEL

    if model == AGENT_MODEL_ID:
        return DEFAULT_MODEL

    _ensure_model_available(model)

    return model

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
        max_tokens=LLM_MAX_COMPLETION_TOKENS,
        timeout=LLM_TIMEOUT_SECONDS,
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
        max_tokens=LLM_MAX_COMPLETION_TOKENS,
        timeout=LLM_TIMEOUT_SECONDS,
    )

    return response.choices[0].message


def chat_with_tools_stream(messages, tools, model=DEFAULT_MODEL, tool_choice="auto"):
    """Return an OpenAI-compatible streaming tool-call response iterator.

    The executor assembles streamed tool-call argument fragments before it
    invokes a tool.  Keeping the transport helper here lets normal and
    durable agent runs share the same model validation and client setup.
    """

    _ensure_model_available(model)
    client = get_client()

    return client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=0,
        max_tokens=LLM_MAX_COMPLETION_TOKENS,
        timeout=LLM_TIMEOUT_SECONDS,
        stream=True,
    )
