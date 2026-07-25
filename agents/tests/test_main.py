import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app import main


def request(stream: bool = False) -> main.OpenAIChatCompletionRequest:
    return main.OpenAIChatCompletionRequest(
        model=main.AGENT_MODEL_ID,
        messages=[main.OpenAIChatMessage(role="user", content="hello")],
        stream=stream,
    )


def test_verify_api_key_requires_valid_bearer_token(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "secret")
    monkeypatch.setattr(main, "ALLOW_INSECURE_NO_AUTH", False)

    assert main.verify_api_key("Bearer secret") is True
    with pytest.raises(HTTPException, match="Invalid API key"):
        main.verify_api_key("Bearer wrong")
    with pytest.raises(HTTPException, match="Missing authorization"):
        main.verify_api_key(None)


def test_openai_chat_rejects_unknown_model():
    bad_request = main.OpenAIChatCompletionRequest(
        model="unknown", messages=[main.OpenAIChatMessage(role="user", content="hello")]
    )

    with pytest.raises(HTTPException, match="only serves"):
        asyncio.run(main.openai_chat(bad_request, None))


def test_openai_chat_returns_openai_shape():
    with patch("app.main.run_in_threadpool", new=AsyncMock(return_value={"answer": "done"})):
        response = asyncio.run(main.openai_chat(request(), None))

    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"] == {"role": "assistant", "content": "done"}


def test_openai_stream_returns_sse_and_done_marker():
    def streamed_agent(*args, **kwargs):
        kwargs["on_token"]("done")
        return {"answer": "done"}

    async def collect():
        with patch("app.main.run_agent", side_effect=streamed_agent):
            response = await main.openai_chat(request(stream=True), None)
            return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(collect())
    initial = json.loads(chunks[0].removeprefix("data: ").strip())

    assert initial["object"] == "chat.completion.chunk"
    assert any('"content": "done"' in chunk for chunk in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"
