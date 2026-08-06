import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.core.cancellation import cancellation_context
from app.core.exceptions import RunCancelled
from app.llm import client


def _completion_client():
    completion = Mock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )
    )
    return (
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=completion))
        ),
        completion,
    )


@patch("app.llm.client._ensure_model_available")
def test_tool_completion_has_bounded_local_inference_settings(_available):
    fake_client, completion = _completion_client()

    with patch("app.llm.client.get_client", return_value=fake_client):
        result = client.chat_with_tools(
            [{"role": "user", "content": "hello"}], [], model="qwen3-8b"
        )

    assert result.content == "ok"
    assert completion.call_args.kwargs["max_tokens"] == client.LLM_MAX_COMPLETION_TOKENS
    assert completion.call_args.kwargs["timeout"] == client.LLM_TIMEOUT_SECONDS


@patch("app.llm.client._ensure_model_available")
def test_tool_completion_accepts_profile_output_budget(_available):
    fake_client, completion = _completion_client()

    with patch("app.llm.client.get_client", return_value=fake_client):
        client.chat_with_tools([], [], model="coder", max_tokens=640)

    assert completion.call_args.kwargs["max_tokens"] == 640


@patch("app.llm.client._ensure_model_available")
def test_tool_completion_accepts_profile_timeout(_available):
    fake_client, completion = _completion_client()

    with patch("app.llm.client.get_client", return_value=fake_client):
        client.chat_with_tools([], [], model="coder", timeout_seconds=300)

    assert completion.call_args.kwargs["timeout"] == 300


@patch("app.llm.client._ensure_model_available")
def test_plain_completion_accepts_structured_json_mode(_available):
    fake_client, completion = _completion_client()

    with patch("app.llm.client.get_client", return_value=fake_client):
        client.chat(
            [{"role": "user", "content": "route this"}],
            model="quick",
            response_format={"type": "json_object"},
        )

    assert completion.call_args.kwargs["response_format"] == {"type": "json_object"}


@patch("app.llm.client._ensure_model_available")
def test_plain_completion_accepts_operation_specific_timeout(_available):
    fake_client, completion = _completion_client()

    with patch.object(client, "get_client", return_value=fake_client):
        client.chat(
            [{"role": "user", "content": "route this"}],
            timeout_seconds=240,
        )

    assert completion.call_args.kwargs["timeout"] == 240


@patch("app.llm.client.OpenAI")
def test_client_disables_sdk_retries_for_local_inference(openai):
    previous = client._client
    client._client = None
    try:
        client.get_client()
    finally:
        client._client = previous

    assert openai.call_args.kwargs["max_retries"] == 0


@patch("app.llm.client._ensure_model_available")
def test_stream_holds_llm_slot_for_the_complete_iteration(_available):
    events = []

    class Slot:
        def __enter__(self):
            events.append("acquired")

        def __exit__(self, *_args):
            events.append("released")

    fake_client, completion = _completion_client()
    completion.return_value = iter(["first", "second"])
    with (
        patch("app.llm.client.get_client", return_value=fake_client),
        patch.object(client, "_llm_slots", Slot()),
    ):
        stream = client.chat_with_tools_stream([], [], model="coder")
        assert events == []
        assert list(stream) == ["first", "second"]

    assert events == ["acquired", "released"]
    assert completion.call_args.kwargs["stream"] is True


@patch("app.llm.client._ensure_model_available")
def test_stream_closes_transport_when_run_is_cancelled(_available):
    class Response:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            yield "first"
            yield "second"

        def close(self):
            self.closed = True

    response = Response()
    fake_client, completion = _completion_client()
    completion.return_value = response
    checks = iter([False, True])

    with (
        patch("app.llm.client.get_client", return_value=fake_client),
        pytest.raises(RunCancelled),
    ):
        list(
            client.chat_with_tools_stream(
                [],
                [],
                model="coder",
                should_cancel=lambda: next(checks),
            )
        )

    assert response.closed is True


@patch("app.llm.client._ensure_model_available")
def test_stream_wall_clock_deadline_stops_a_heartbeat_stall(_available):
    class StalledResponse:
        def __init__(self):
            self.closed = False
            self.released = threading.Event()

        def __iter__(self):
            self.released.wait()
            return
            yield  # pragma: no cover - keeps this method an iterator

        def close(self):
            self.closed = True
            self.released.set()

    response = StalledResponse()
    fake_client, completion = _completion_client()
    completion.return_value = response

    started = time.monotonic()
    with (
        patch("app.llm.client.get_client", return_value=fake_client),
        pytest.raises(TimeoutError, match="deadline"),
    ):
        list(client.chat_with_tools_stream([], [], model="coder", timeout_seconds=0.05))

    assert time.monotonic() - started < 1
    assert response.closed is True


@patch("app.llm.client._ensure_model_available")
def test_planner_completion_streams_when_cancellation_context_exists(_available):
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="one"))]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=" two"))]
        ),
    ]
    fake_client, completion = _completion_client()
    completion.return_value = iter(chunks)

    with (
        patch("app.llm.client.get_client", return_value=fake_client),
        cancellation_context(lambda: False),
    ):
        assert client.chat([], model="coder") == "one two"

    assert completion.call_args.kwargs["stream"] is True


class _Transient(Exception):
    pass


class _GatewayError(Exception):
    pass


def test_is_transient_error_classifies_gateway_statuses():
    assert client._is_transient_error(_GatewayError("boom"))
    for status in (408, 429, 500, 502, 503, 504):
        assert client._is_transient_error(
            SimpleNamespace(status_code=status, response=None)
        )
    assert not client._is_transient_error(ValueError("model not found"))
    assert not client._is_transient_error(SimpleNamespace(status_code=400))
    assert not client._is_transient_error(
        SimpleNamespace(status_code=None, response=SimpleNamespace(status_code=401))
    )
    assert not client._is_transient_error(RunCancelled())


def test_is_transient_error_sees_connection_and_timeout_names():
    assert client._is_transient_error(TimeoutError("read timed out"))
    assert client._is_transient_error(ConnectionError("connection refused"))


def test_with_transient_retry_succeeds_after_gateway_error():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _GatewayError("upstream 503")
        return "ok"

    with patch("app.llm.client.time.sleep"):
        assert client._with_transient_retry(flaky) == "ok"
    assert calls["n"] == 2


def test_with_transient_retry_gives_up_after_retries():
    calls = {"n": 0}

    def always_flaky():
        calls["n"] += 1
        raise _GatewayError("upstream 503")

    with (
        patch("app.llm.client.time.sleep"),
        pytest.raises(_GatewayError),
    ):
        client._with_transient_retry(always_flaky)
    assert calls["n"] == client.LLM_MAX_RETRIES + 1


def test_with_transient_retry_does_not_retry_permanent_errors():
    calls = {"n": 0}

    def permanent():
        calls["n"] += 1
        raise ValueError("model not found")

    with (
        patch("app.llm.client.time.sleep"),
        pytest.raises(ValueError),
    ):
        client._with_transient_retry(permanent)
    assert calls["n"] == 1
