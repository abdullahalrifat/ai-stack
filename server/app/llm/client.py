import json
import logging
import os
import queue
import threading
import time
from types import SimpleNamespace

from jarvis_core import InferenceClient, InferenceClientError, InferenceConfig

from ..core.cancellation import (
    has_cancellation_context,
    raise_if_cancelled,
)
from ..core.config import (
    DEFAULT_MODEL,
    LLM_MAX_COMPLETION_TOKENS,
    LLM_MAX_RETRIES,
    LLM_RETRY_BACKOFF_SECONDS,
    LLM_TIMEOUT_SECONDS,
    MAX_CONCURRENT_LLM_CALLS,
    MODEL_LIST_CACHE_SECONDS,
)
from .cache import default_cache, msg_key, make_message_like
from .scheduler import default_scheduler
from .metrics import incr, record_timing, get_metrics
from ..core.exceptions import RunCancelled

logger = logging.getLogger(__name__)

_client = None
_inference_client: InferenceClient | None = None
_model_cache: dict = {"models": None, "fetched_at": 0.0}
_llm_slots = threading.BoundedSemaphore(MAX_CONCURRENT_LLM_CALLS)


def _to_namespace(value):
    """Adapt Core's provider-neutral JSON payloads to legacy call-site shapes."""
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _to_namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    return value


class _CoreCompletions:
    def __init__(self, client: InferenceClient):
        self._client = client

    def create(self, **kwargs):
        stream = bool(kwargs.pop("stream", False))
        timeout = kwargs.pop("timeout", None)
        model = kwargs.pop("model")
        messages = kwargs.pop("messages")
        tools = kwargs.pop("tools", None)
        max_tokens = kwargs.pop("max_tokens", None)
        if stream:
            return (
                _to_namespace(chunk)
                for chunk in self._client.stream(
                    model=model,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    **kwargs,
                )
            )
        return _to_namespace(
            self._client.complete(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                timeout=timeout,
                **kwargs,
            )
        )


class _CoreInferenceAdapter:
    """Preserve the app's small OpenAI-shaped call surface over Core transport."""

    def __init__(self, client: InferenceClient):
        self._client = client
        self.chat = SimpleNamespace(
            completions= _CoreCompletions(client)
        )

    def list_models(self):
        return self._client.list_models()


def _is_transient_error(exc: BaseException) -> bool:
    """True when a retry could plausibly succeed: transport hiccups, timeouts,
    rate limits, and gateway 5xx. Model-not-found and logic errors are not."""
    if isinstance(exc, RunCancelled):
        return False
    if isinstance(exc, InferenceClientError):
        # Do not replay requests after a transport timeout: Ollama may still be
        # generating the original response on its single inference queue.
        return exc.status_code in (408, 429, 500, 502, 503, 504)
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status in (408, 429, 500, 502, 503, 504):
        return True
    name = type(exc).__name__.lower()
    return any(
        token in name for token in ("timeout", "connection", "unavailable", "gateway")
    )


def _with_transient_retry(call):
    """Run `call`, retrying transient transport/gateway failures with backoff."""
    last_exc = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            return call()
        except RunCancelled:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt >= LLM_MAX_RETRIES or not _is_transient_error(exc):
                raise
            logger.warning(
                "Transient LLM error (attempt %d/%d): %s",
                attempt + 1,
                LLM_MAX_RETRIES,
                exc,
            )
            time.sleep(LLM_RETRY_BACKOFF_SECONDS * (2**attempt))
    raise last_exc  # pragma: no cover - defensive; loop always returns or raises


def _iter_stream_with_deadline(response, timeout_seconds: int, should_cancel=None):
    """Iterate an SSE response with a wall-clock deadline and cancellation.

    HTTP read timeouts can be reset indefinitely by gateway heartbeats. A
    small producer thread lets the owning run continue polling cancellation
    and enforce an overall inference deadline even when the stream yields no
    model chunks.
    """

    items: queue.Queue = queue.Queue()
    finished = object()

    def produce() -> None:
        try:
            for chunk in response:
                items.put((True, chunk))
        except BaseException as exc:
            items.put((False, exc))
        finally:
            items.put((True, finished))

    worker = threading.Thread(target=produce, daemon=True, name="llm-stream-reader")
    worker.start()
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            if should_cancel is not None:
                if should_cancel():
                    raise RunCancelled()
            else:
                raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Model stream exceeded the {timeout_seconds}-second deadline"
                )
            try:
                succeeded, item = items.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if not succeeded:
                raise item
            if item is finished:
                break
            yield item
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()
        worker.join(timeout=1)


def _llm_endpoint() -> tuple[str, str]:
    """Return the dedicated inference gateway endpoint."""
    base_url = os.getenv("INFERENCE_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("INFERENCE_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not base_url:
        raise RuntimeError("INFERENCE_BASE_URL is required; configure jarvis-inference")
    return base_url, api_key


def _get_inference_client() -> InferenceClient:
    global _inference_client
    if _inference_client is None:
        base_url, api_key = _llm_endpoint()
        _inference_client = InferenceClient(
            InferenceConfig(
                base_url=base_url,
                api_key=api_key,
                timeout=LLM_TIMEOUT_SECONDS,
                user_agent="ai-stack-server",
            )
        )
    return _inference_client


def get_client():
    """Return the shared Core transport behind the existing app call surface."""
    global _client
    if _client is None:
        _client = _CoreInferenceAdapter(_get_inference_client())
    return _client

def resolve_agent_model(model: str | None):

    if not model:
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

    models = [
        str(item["id"])
        for item in _get_inference_client().list_models()
        if item.get("id")
    ]

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
        # Attempt to serve repeated identical requests from an in-memory LRU
        # cache to avoid redundant inference on local models.
        key = msg_key(messages, model, kwargs["max_tokens"])
        cached = default_cache.get(key)
        if cached is not None:
            incr("llm.cache_hit")
            return cached.content

        incr("llm.request")

        # Use the scheduler to smooth spikes and limit concurrency. The
        # semaphore still enforces a strict concurrency cap; the scheduler
        # serializes requests through a worker pool to stabilize load.
        def do_request():
            with _llm_slots:
                return _with_transient_retry(
                    lambda: client.chat.completions.create(**kwargs)
                )

        start = time.perf_counter()
        response = default_scheduler.submit_sync(do_request, key=key)
        elapsed = time.perf_counter() - start
        record_timing("llm.latency", elapsed)

        content = response.choices[0].message.content
        # Cache the simple message representation (content only here).
        try:
            default_cache.set(key, make_message_like({"content": content}))
        except Exception:
            logger.exception("Failed to cache LLM response")
        return content

    # A streaming request gives cooperative cancellation a transport handle.
    # This is used for durable planning/routing calls; direct API callers keep
    # the simpler non-streaming request above.
    parts: list[str] = []
    stream = None
    with _llm_slots:
        try:
            stream = _with_transient_retry(
                lambda: client.chat.completions.create(**kwargs, stream=True)
            )
            for chunk in _iter_stream_with_deadline(
                stream, timeout_seconds or LLM_TIMEOUT_SECONDS
            ):
                choices = getattr(chunk, "choices", None) or []
                if choices:
                    content = getattr(choices[0].delta, "content", None)
                    if content:
                        parts.append(content)
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

    # Try cache for non-streaming tool calls. We serialize both the messages
    # and the tools descriptors into the key so identical tool-call requests
    # are de-duplicated.
    key = default_cache.make_key(messages, tools, model, max_tokens)
    cached = default_cache.get(key)
    if cached is not None:
        incr("llm.cache_hit")
        return make_message_like(cached)

    incr("llm.request")
    start = time.perf_counter()
    with _llm_slots:
        response = _with_transient_retry(
            lambda: client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=0,
                max_tokens=max_tokens or LLM_MAX_COMPLETION_TOKENS,
                timeout=timeout_seconds or LLM_TIMEOUT_SECONDS,
            )
        )
    elapsed = time.perf_counter() - start
    record_timing("llm.latency", elapsed)

    msg = response.choices[0].message
    # Cache a lightweight dict capturing content and tool_calls if present.
    try:
        cached_obj = {
            "content": getattr(msg, "content", None),
            "tool_calls": getattr(msg, "tool_calls", None),
        }
        default_cache.set(key, cached_obj)
    except Exception:
        # Don't let caching failures break model calls.
        logger.exception("Failed to cache LLM tool response")

    return msg


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
        incr("llm.request")
        start = time.perf_counter()
        with _llm_slots:
            try:
                response = _with_transient_retry(
                    lambda: client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        temperature=0,
                        max_tokens=max_tokens or LLM_MAX_COMPLETION_TOKENS,
                        timeout=timeout_seconds or LLM_TIMEOUT_SECONDS,
                        stream=True,
                    )
                )
                for chunk in _iter_stream_with_deadline(
                    response,
                    timeout_seconds or LLM_TIMEOUT_SECONDS,
                    should_cancel=should_cancel,
                ):
                    yield chunk
            finally:
                elapsed = time.perf_counter() - start
                record_timing("llm.latency", elapsed)
                close = getattr(response, "close", None)
                if close is not None:
                    close()

    return stream()


def chat_stream_text(
    messages,
    tools=None,
    model=DEFAULT_MODEL,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    should_cancel=None,
):
    """Yield text fragments from the streaming tool-call iterator.

    This helper normalizes chunk objects into plain strings so callers (e.g.
    CLI) can print partial content as it arrives.
    """
    stream_iter = chat_with_tools_stream(
        messages,
        tools or [],
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        should_cancel=should_cancel,
    )
    # The transport yields chunk-like objects with `.choices` deltas.
    for chunk in stream_iter:
        choices = getattr(chunk, "choices", None) or []
        if choices:
            content = getattr(choices[0].delta, "content", None)
            if content:
                yield content


def get_llm_metrics():
    """Expose combined metrics for monitoring."""
    try:
        from .scheduler import get_scheduler_stats

        return {
            "metrics": get_metrics(),
            "cache": get_llm_cache_stats(),
            "scheduler": get_scheduler_stats(),
        }
    except Exception:
        return {"metrics": get_metrics(), "cache": get_llm_cache_stats()}


def get_llm_cache_stats():
    """Return simple cache stats for observability and tests."""
    try:
        return default_cache.stats()
    except Exception:
        return {"size": 0, "hits": 0, "misses": 0}


def get_llm_instrumentation():
    """Return a combined dict of cache and scheduler stats for instrumentation."""
    try:
        from .scheduler import get_scheduler_stats

        scheduler = get_scheduler_stats()
    except Exception:
        scheduler = None
    return {"cache": get_llm_cache_stats(), "scheduler": scheduler}


def chat_fast(
    messages, max_tokens: int | None = None, timeout_seconds: int | None = None
) -> str:
    """Attempt to answer short/low-latency requests using the configured
    `FAST_MODEL`. Callers can use this when they expect a small, fast reply
    (e.g., request routing, normalization, or light transformations).
    """
    # If a FAST_MODEL isn't configured or the request is large, fall back to
    # the normal `chat` function.
    from ..core.config import FAST_MODEL

    # Heuristic: favor fast model only when the incoming message list is
    # reasonably short and max_tokens small.
    if not messages or len(messages) > 6 or (max_tokens or 0) > 512:
        return chat(messages, max_tokens=max_tokens, timeout_seconds=timeout_seconds)

    try:
        return chat(
            messages,
            model=FAST_MODEL,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        # If the fast model isn't available, degrade to the normal chat.
        logger.exception("FAST_MODEL fast path failed; falling back to default chat")
        return chat(messages, max_tokens=max_tokens, timeout_seconds=timeout_seconds)
