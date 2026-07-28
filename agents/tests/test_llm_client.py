from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.llm import client


def _completion_client():
    completion = Mock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
    ))
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=completion))), completion


@patch("app.llm.client._ensure_model_available")
def test_tool_completion_has_bounded_local_inference_settings(_available):
    fake_client, completion = _completion_client()

    with patch("app.llm.client.get_client", return_value=fake_client):
        result = client.chat_with_tools([{"role": "user", "content": "hello"}], [], model="qwen3-8b")

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
