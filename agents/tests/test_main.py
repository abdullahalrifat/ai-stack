import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api import dependencies, routes, schemas
from app.core.config import AGENT_MODEL_ID


def request(stream: bool = False) -> schemas.OpenAIChatCompletionRequest:
    return schemas.OpenAIChatCompletionRequest(
        model=AGENT_MODEL_ID,
        messages=[schemas.OpenAIChatMessage(role="user", content="hello")],
        stream=stream,
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

    with pytest.raises(HTTPException, match="only serves"):
        asyncio.run(routes.openai_chat(bad_request, None))


def test_openai_chat_returns_openai_shape():
    with patch("app.api.routes.run_in_threadpool", new=AsyncMock(return_value={"answer": "done"})):
        response = asyncio.run(routes.openai_chat(request(), None))

    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"] == {"role": "assistant", "content": "done"}


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
