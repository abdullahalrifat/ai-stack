import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api import dependencies, routes, schemas
from app.api.context import compact_openai_messages, openai_prompt
from app.api.profiles import PROFILES
from app.core.config import AGENT_MODEL_ID, DEFAULT_MODEL, FAST_MODEL, FINANCE_LLM_TIMEOUT_SECONDS, FINANCE_MAX_COMPLETION_TOKENS, FINANCE_MODEL


def request(stream: bool = False) -> schemas.OpenAIChatCompletionRequest:
    return schemas.OpenAIChatCompletionRequest(
        model=AGENT_MODEL_ID,
        messages=[schemas.OpenAIChatMessage(role="user", content="hello")],
        stream=stream,
    )


@pytest.fixture(autouse=True)
def isolated_workspace_resolution(monkeypatch, tmp_path):
    """Keep route unit tests independent from Docker's /workspace mount.

    Workspace validation and prompt-based narrowing are covered in
    test_filesystem.py. These tests mock the agent call and only exercise the
    OpenAI response contract, so they must not depend on a container path
    that does not exist on GitHub Actions.
    """

    monkeypatch.setattr(
        routes,
        "resolve_request_workspace",
        lambda _workspace, _prompt: str(tmp_path),
    )


def test_verify_api_key_requires_valid_bearer_token(monkeypatch):
    monkeypatch.setattr(dependencies, "AGENT_API_KEY", "secret")
    monkeypatch.setattr(dependencies, "ALLOW_INSECURE_NO_AUTH", False)

    assert dependencies.verify_api_key("Bearer secret") is True
    with pytest.raises(HTTPException, match="Invalid API key"):
        dependencies.verify_api_key("Bearer wrong")
    with pytest.raises(HTTPException, match="Missing authorization"):
        dependencies.verify_api_key(None)


def test_openai_chat_rejects_unknown_model():
    bad_request = schemas.OpenAIChatCompletionRequest(
        model="unknown", messages=[schemas.OpenAIChatMessage(role="user", content="hello")]
    )

    with pytest.raises(HTTPException, match="Unknown agent profile"):
        asyncio.run(routes.openai_chat(bad_request, None))


def test_openai_chat_rejects_direct_write_request():
    write_request = schemas.OpenAIChatCompletionRequest(
        model="code", messages=[schemas.OpenAIChatMessage(role="user", content="edit it")], allow_write=True
    )
    with pytest.raises(HTTPException, match="read-only"):
        asyncio.run(routes.openai_chat(write_request, None))


def test_openai_chat_returns_openai_shape():
    with patch("app.api.routes.run_in_threadpool", new=AsyncMock(return_value={"answer": "done"})):
        response = asyncio.run(routes.openai_chat(request(), None))

    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"] == {"role": "assistant", "content": "done"}


def test_openai_chat_uses_unique_conversation_id_when_client_omits_one():
    with patch("app.api.routes.run_in_threadpool", new=AsyncMock(return_value={"answer": "done"})) as runner:
        asyncio.run(routes.openai_chat(request(), None))

    assert runner.call_args.args[2] != "default"


def test_openai_context_compaction_preserves_latest_request_and_bounds_payload():
    messages = [
        schemas.OpenAIChatMessage(role="system", content="s" * 4_000),
        schemas.OpenAIChatMessage(role="user", content="old question " + "a" * 4_000),
        schemas.OpenAIChatMessage(role="assistant", content="old answer " + "b" * 4_000),
        schemas.OpenAIChatMessage(role="user", content="LATEST REQUEST: inspect routes.py " + "c" * 4_000),
    ]

    compacted = compact_openai_messages(messages, max_chars=3_000)
    prompt = openai_prompt(messages, max_chars=3_000)

    assert all(message["role"] != "system" for message in compacted)
    assert compacted[-1]["role"] == "user"
    assert "LATEST REQUEST" in compacted[-1]["content"]
    assert len("".join(message["content"] for message in compacted)) <= 3_100
    assert "LATEST REQUEST" in prompt
    assert "s" * 100 not in prompt


def test_openai_context_compaction_prefers_newest_user_over_large_history():
    messages = [
        schemas.OpenAIChatMessage(role="user", content="old " + "x" * 8_000),
        schemas.OpenAIChatMessage(role="assistant", content="reply " + "y" * 8_000),
        schemas.OpenAIChatMessage(role="user", content="please fix the failing test"),
    ]

    compacted = compact_openai_messages(messages, max_chars=2_000)

    assert compacted[-1] == {"role": "user", "content": "please fix the failing test"}


def test_openai_prompt_marks_history_as_reference_and_latest_user_as_task():
    messages = [
        schemas.OpenAIChatMessage(role="system", content="Follow Continue's internal protocol."),
        schemas.OpenAIChatMessage(role="user", content="Earlier request"),
        schemas.OpenAIChatMessage(role="assistant", content="Earlier answer"),
        schemas.OpenAIChatMessage(role="user", content="Review this codebase for gaps."),
    ]

    prompt = openai_prompt(messages, max_chars=4_000)

    assert "Continue's internal protocol" not in prompt
    assert "reference only" in prompt
    assert prompt.endswith("Review this codebase for gaps.")


def test_openai_research_profile_forces_research_mode():
    research_request = schemas.OpenAIChatCompletionRequest(
        model="research", messages=[schemas.OpenAIChatMessage(role="user", content="summarize this")]
    )
    with patch("app.api.routes.run_in_threadpool", new=AsyncMock(return_value={"answer": "done"})) as runner:
        asyncio.run(routes.openai_chat(research_request, None))

    assert runner.call_args.kwargs["force_research"] is True


def test_openai_models_expose_only_central_router_agent():
    ids = {model["id"] for model in routes.models()["data"]}

    assert ids == {AGENT_MODEL_ID}


def test_auto_profile_uses_fast_model_while_code_uses_default_model():
    assert PROFILES["auto"].model == FAST_MODEL
    assert PROFILES["code"].model == DEFAULT_MODEL
    assert PROFILES["finance"].model == FINANCE_MODEL
    assert PROFILES["finance"].max_completion_tokens == FINANCE_MAX_COMPLETION_TOKENS
    assert PROFILES["finance"].timeout_seconds == FINANCE_LLM_TIMEOUT_SECONDS


def test_available_models_is_a_public_gateway_catalog():
    with patch("app.api.routes.get_available_models", return_value=["qwen3-8b", "reasoning"]):
        assert routes.available_models() == {"models": ["qwen3-8b", "reasoning"]}


def test_image_generation_status_is_explicit_when_unconfigured(monkeypatch):
    monkeypatch.setattr(routes, "IMAGE_GENERATION_URL", "")

    assert routes.image_generation_status() == {"available": False, "provider": None}


def test_openai_stream_returns_sse_and_done_marker():
    def streamed_agent(*args, **kwargs):
        kwargs["on_token"]("done")
        return {"answer": "done"}

    async def collect():
        with patch("app.api.routes.run_agent", side_effect=streamed_agent):
            response = await routes.openai_chat(request(stream=True), None)
            return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(collect())
    initial = json.loads(chunks[0].removeprefix("data: ").strip())

    assert initial["object"] == "chat.completion.chunk"
    assert any('"content": "done"' in chunk for chunk in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"
