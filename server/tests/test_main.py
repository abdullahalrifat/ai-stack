import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import dependencies, routes, schemas
from app.api.context import compact_openai_messages, openai_prompt
from app.api.profiles import PROFILES, resolve_workflow
from app.core.config import (
    DEFAULT_MODEL,
    FAST_MODEL,
    FINANCE_LLM_TIMEOUT_SECONDS,
    FINANCE_MAX_COMPLETION_TOKENS,
    FINANCE_MODEL,
    RESEARCH_MODEL,
)
from app import main as app_main


def request(stream: bool = False) -> schemas.OpenAIChatCompletionRequest:
    return schemas.OpenAIChatCompletionRequest(
        model=DEFAULT_MODEL,
        messages=[schemas.OpenAIChatMessage(role="user", content="hello")],
        stream=stream,
    )


def test_worker_reconciliation_resubmits_and_cleans_sandboxes(monkeypatch):
    store = MagicMock()
    store.recover_interrupted_runs.return_value = (["queued-1"], [])
    store.sandboxes_needing_cleanup.return_value = [
        {
            "id": "failed-1",
            "repository_path": "/repo",
            "sandbox_path": "/sandboxes/failed-1",
        }
    ]
    removed = []
    submitted = []
    monkeypatch.setattr(app_main, "get_run_store", lambda: store)
    monkeypatch.setattr(
        app_main,
        "remove_sandbox",
        lambda repository, path: removed.append((repository, path)),
    )
    monkeypatch.setattr(app_main, "submit_run", submitted.append)

    app_main.reconcile_runs_once()

    assert removed == [("/repo", "/sandboxes/failed-1")]
    store.mark_sandbox_cleaned.assert_called_once_with("failed-1")
    assert submitted == ["queued-1"]


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
        model="unknown",
        messages=[schemas.OpenAIChatMessage(role="user", content="hello")],
    )

    with pytest.raises(HTTPException, match="Unknown agent profile"):
        asyncio.run(routes.openai_chat(bad_request, None))


def test_openai_chat_rejects_direct_write_request():
    write_request = schemas.OpenAIChatCompletionRequest(
        model="code",
        messages=[schemas.OpenAIChatMessage(role="user", content="edit it")],
        allow_write=True,
    )
    with pytest.raises(HTTPException, match="read-only"):
        asyncio.run(routes.openai_chat(write_request, None))


def test_openai_chat_returns_openai_shape():
    with patch(
        "app.api.routes.run_in_threadpool",
        new=AsyncMock(return_value={"answer": "done"}),
    ):
        response = asyncio.run(routes.openai_chat(request(), None))

    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"] == {"role": "assistant", "content": "done"}


def test_openai_chat_uses_unique_conversation_id_when_client_omits_one():
    with patch(
        "app.api.routes.run_in_threadpool",
        new=AsyncMock(return_value={"answer": "done"}),
    ) as runner:
        asyncio.run(routes.openai_chat(request(), None))

    assert runner.call_args.args[2] != "default"


def test_openai_context_compaction_preserves_latest_request_and_bounds_payload():
    messages = [
        schemas.OpenAIChatMessage(role="system", content="s" * 4_000),
        schemas.OpenAIChatMessage(role="user", content="old question " + "a" * 4_000),
        schemas.OpenAIChatMessage(
            role="assistant", content="old answer " + "b" * 4_000
        ),
        schemas.OpenAIChatMessage(
            role="user", content="LATEST REQUEST: inspect routes.py " + "c" * 4_000
        ),
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
        schemas.OpenAIChatMessage(
            role="system", content="Follow Continue's internal protocol."
        ),
        schemas.OpenAIChatMessage(role="user", content="Earlier request"),
        schemas.OpenAIChatMessage(role="assistant", content="Earlier answer"),
        schemas.OpenAIChatMessage(
            role="user", content="Review this codebase for gaps."
        ),
    ]

    prompt = openai_prompt(messages, max_chars=4_000)

    assert "Continue's internal protocol" not in prompt
    assert "reference only" in prompt
    assert prompt.endswith("Review this codebase for gaps.")


def test_openai_research_profile_forces_research_mode():
    research_request = schemas.OpenAIChatCompletionRequest(
        model=RESEARCH_MODEL,
        messages=[schemas.OpenAIChatMessage(role="user", content="summarize this")],
    )
    with patch(
        "app.api.routes.run_in_threadpool",
        new=AsyncMock(return_value={"answer": "done"}),
    ) as runner:
        asyncio.run(routes.openai_chat(research_request, None))

    assert runner.call_args.kwargs["force_research"] is False


def test_openai_models_expose_only_central_router_agent():
    ids = {model["id"] for model in routes.models()["data"]}

    assert ids == set(PROFILES)


def test_cancel_reports_immediate_terminal_status_for_queued_run():
    class Store:
        def request_cancel(self, _run_id):
            return True

        def get_run(self, _run_id):
            return {"status": "cancelled"}

        def append_event(self, run_id, event_type, payload):
            return {
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
            }

    publisher = MagicMock()
    with (
        patch("app.api.routes.get_run_store", return_value=Store()),
        patch("app.api.routes.get_event_publisher") as get_publisher,
    ):
        get_publisher.return_value = publisher
        result = asyncio.run(routes.cancel("run-1"))

    assert result == {
        "protocol_version": 1,
        "run_id": "run-1",
        "status": "cancelled",
    }
    publisher.publish.assert_called_once()


def test_create_run_records_foreground_client_lease(monkeypatch, tmp_path):
    class Store:
        def create_run(self, **fields):
            assert fields["client_id"] == "terminal-1"
            assert fields["client_lease_seconds"] == routes.RUN_CLIENT_LEASE_SECONDS
            return "run-1"

    monkeypatch.setattr(routes, "get_run_store", lambda: Store())
    monkeypatch.setattr(routes, "submit_run", lambda _run_id: None)
    result = asyncio.run(
        routes.create_run(
            schemas.RunRequest(
                task="inspect the repository",
                workspace=str(tmp_path),
                client_id="terminal-1",
            )
        )
    )

    assert result == {
        "run_id": "run-1",
        "status": "queued",
        "client_id": "terminal-1",
        "protocol_version": 1,
    }


def test_capabilities_advertise_foreground_client_leases():
    result = routes.capabilities()

    assert result["api_version"] == "1"
    assert result["protocol"]["event_schema"] == 1
    assert "client_leases" in result["features"]


def test_run_event_route_is_registered_to_stream_handler():
    route = next(
        item for item in routes.router.routes if item.path == "/runs/{run_id}/events"
    )

    assert route.endpoint is routes.run_events


def test_run_event_stream_sends_heartbeat_while_run_is_quiet(monkeypatch):
    class Store:
        def get_run(self, _run_id):
            return {"status": "running"}

        def events_after(self, _run_id, _last_id):
            return []

    class Publisher:
        def subscribe(self, _run_id):
            raise RuntimeError("Redis unavailable in unit test")

    async def first_chunk():
        response = await routes.run_events("run-1")
        iterator = response.body_iterator
        chunk = await anext(iterator)
        await iterator.aclose()
        return chunk

    monkeypatch.setattr(routes, "get_run_store", lambda: Store())
    monkeypatch.setattr(routes, "get_event_publisher", lambda: Publisher())
    monkeypatch.setattr(routes, "RUN_STREAM_HEARTBEAT_SECONDS", 0)

    assert asyncio.run(first_chunk()) == ": heartbeat\n\n"


def test_run_event_stream_versions_terminal_envelope(monkeypatch):
    class Store:
        def get_run(self, _run_id):
            return {"status": "completed"}

        def events_after(self, _run_id, _last_id):
            return []

    class Publisher:
        def subscribe(self, _run_id):
            raise RuntimeError("Redis unavailable in unit test")

    async def first_chunk():
        response = await routes.run_events("run-1")
        iterator = response.body_iterator
        chunk = await anext(iterator)
        await iterator.aclose()
        return chunk

    monkeypatch.setattr(routes, "get_run_store", lambda: Store())
    monkeypatch.setattr(routes, "get_event_publisher", lambda: Publisher())

    raw = asyncio.run(first_chunk())
    event = json.loads(raw.removeprefix("data: ").strip())
    assert event == {
        "protocol_version": 1,
        "schema_version": 1,
        "event_type": "stream_closed",
        "status": "completed",
    }


def test_run_event_stream_renews_and_releases_foreground_lease(monkeypatch):
    renewals = []

    class Store:
        def get_run(self, _run_id):
            return {"status": "running", "client_id": "terminal-1"}

        def events_after(self, _run_id, _last_id):
            return []

        def renew_client_lease(self, run_id, client_id, lease_seconds):
            renewals.append((run_id, client_id, lease_seconds))
            return True

    class Publisher:
        def subscribe(self, _run_id):
            raise RuntimeError("Redis unavailable in unit test")

    async def first_chunk():
        response = await routes.run_events("run-1", client_id="terminal-1")
        iterator = response.body_iterator
        chunk = await anext(iterator)
        await iterator.aclose()
        return chunk

    monkeypatch.setattr(routes, "get_run_store", lambda: Store())
    monkeypatch.setattr(routes, "get_event_publisher", lambda: Publisher())
    monkeypatch.setattr(routes, "RUN_STREAM_HEARTBEAT_SECONDS", 0)

    assert asyncio.run(first_chunk()) == ": heartbeat\n\n"
    assert renewals == [("run-1", "terminal-1", routes.RUN_CLIENT_LEASE_SECONDS)]


def test_auto_profile_uses_default_model_while_quick_uses_fast_model():
    assert resolve_workflow("quick").model == FAST_MODEL
    assert resolve_workflow("quick").model == FAST_MODEL
    assert resolve_workflow("code").model == DEFAULT_MODEL
    assert resolve_workflow("finance").model == FINANCE_MODEL
    assert resolve_workflow("finance").max_completion_tokens == FINANCE_MAX_COMPLETION_TOKENS
    assert resolve_workflow("finance").timeout_seconds == FINANCE_LLM_TIMEOUT_SECONDS


def test_available_models_is_a_public_gateway_catalog():
    with patch(
        "app.api.routes.get_available_models", return_value=["qwen3-4b"]
    ):
        assert routes.available_models() == {"models": ["qwen3-4b"]}


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
