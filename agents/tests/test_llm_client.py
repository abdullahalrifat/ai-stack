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


@patch("app.llm.client.OpenAI")
def test_client_disables_sdk_retries_for_local_inference(openai):
    previous = client._client
    client._client = None
    try:
        client.get_client()
    finally:
        client._client = previous

    assert openai.call_args.kwargs["max_retries"] == 0
