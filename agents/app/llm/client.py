import logging
import os
import threading
import time

import requests
from openai import OpenAI

from ..core.cancellation import (
    has_cancellation_context,
    raise_if_cancelled,
)
from ..core.config import (
    AGENT_MODEL_ID,
    DEFAULT_MODEL,
    LLM_MAX_COMPLETION_TOKENS,
    LLM_TIMEOUT_SECONDS,
    MAX_CONCURRENT_LLM_CALLS,
    MODEL_LIST_CACHE_SECONDS,
)
from ..core.exceptions import RunCancelled

logger = logging.getLogger(__name__)

_client = None
_model_cache: dict = {"models": None, "fetched_at": 0.0}
_llm_slots = threading.BoundedSemaphore(MAX_CONCURRENT_LLM_CALLS)


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


def chat(
    messages,
    model=DEFAULT_MODEL,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    timeout_seconds: int | None = None,
) -> str:
    """Plain-text completion, no tool calling. Used by the planner and by
    internal helpers like context compaction."""

    _ensure_model_available(model)
    client = get_client()

    kwargs = dict(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=max_tokens or LLM_MAX_COMPLETION_TOKENS,
        timeout=timeout_seconds or LLM_TIMEOUT_SECONDS,
    )
    if response_format is not None:
        kwargs["response_format"] = response_format
    if not has_cancellation_context():
        with _llm_slots:
            response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    # A streaming request gives cooperative cancellation a transport handle.
    # This is used for durable planning/routing calls; direct API callers keep
    # the simpler non-streaming request above.
    parts: list[str] = []
    stream = None
    with _llm_slots:
        try:
            stream = client.chat.completions.create(**kwargs, stream=True)
            for chunk in stream:
                raise_if_cancelled()
                choices = getattr(chunk, "choices", None) or []
                if choices:
                    content = getattr(choices[0].delta, "content", None)
                    if content:
                        parts.append(content)
            raise_if_cancelled()
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()
    return "".join(parts)


def chat_with_tools(
    messages,
    tools,
    model=DEFAULT_MODEL,
    tool_choice="auto",
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
):
    """Completion using native function calling.

    Returns the raw response message object, exposing both `.content` and
    `.tool_calls` so callers can branch on whichever the model produced,
    instead of asking the model to hand-write JSON tool calls in prose.
    """

    _ensure_model_available(model)
    client = get_client()

    with _llm_slots:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=0,
            max_tokens=max_tokens or LLM_MAX_COMPLETION_TOKENS,
            timeout=timeout_seconds or LLM_TIMEOUT_SECONDS,
        )

    return response.choices[0].message


def chat_with_tools_stream(
    messages,
    tools,
    model=DEFAULT_MODEL,
    tool_choice="auto",
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    should_cancel=None,
):
    """Return an OpenAI-compatible streaming tool-call response iterator.

    The executor assembles streamed tool-call argument fragments before it
    invokes a tool.  Keeping the transport helper here lets normal and
    durable agent runs share the same model validation and client setup.
    """

    _ensure_model_available(model)
    client = get_client()

    def stream():
        response = None
        with _llm_slots:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=0,
                    max_tokens=max_tokens or LLM_MAX_COMPLETION_TOKENS,
                    timeout=timeout_seconds or LLM_TIMEOUT_SECONDS,
                    stream=True,
                )
                for chunk in response:
                    if should_cancel is not None:
                        if should_cancel():
                            raise RunCancelled()
                    else:
                        raise_if_cancelled()
                    yield chunk
                if should_cancel is not None:
                    if should_cancel():
                        raise RunCancelled()
                else:
                    raise_if_cancelled()
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()

    return stream()
