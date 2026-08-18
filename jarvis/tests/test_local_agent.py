import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from aistack_cli.client import APIError
from aistack_cli.local_agent import (
    LocalConfig,
    LocalTools,
    ModelProvider,
    resolve_local_config,
    run_local_agent,
)


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit=-1):
        return self.payload


def config(tmp_path, **values):
    defaults = dict(
        provider="openai",
        model="coder",
        api_key="secret",
        base_url="https://model.test/v1",
        workspace=tmp_path,
        allow_edits=False,
        max_steps=5,
    )
    defaults.update(values)
    return LocalConfig(**defaults)


def test_resolve_local_config_requires_endpoint_and_key(monkeypatch, tmp_path):
    for name in (
        "AISTACK_LOCAL_BASE_URL",
        "AISTACK_MODEL_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    args = SimpleNamespace(
        provider="openai",
        model="coder",
        base_url=None,
        api_key_env=None,
        local_workspace=str(tmp_path),
        workspace=None,
        write=False,
        accept_edits=False,
        max_steps=5,
        timeout=30,
    )
    with pytest.raises(APIError, match="No model endpoint"):
        resolve_local_config(args)


def test_openai_provider_sends_tools_and_normalizes_call(tmp_path):
    captured = {}

    def opener(request, timeout):
        captured["request"] = request
        return Response(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"README.md"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

    provider = ModelProvider(config(tmp_path), opener=opener)
    _text, calls, _raw = provider.complete(
        [{"role": "user", "content": "inspect"}],
        [
            {
                "name": "read_file",
                "description": "read",
                "parameters": {"type": "object"},
            }
        ],
    )

    assert calls == [
        {
            "id": "call-1",
            "name": "read_file",
            "arguments": {"path": "README.md"},
        }
    ]
    request = captured["request"]
    assert request.full_url == "https://model.test/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer secret"
    assert json.loads(request.data)["tools"][0]["type"] == "function"


def test_anthropic_provider_uses_native_messages_api(tmp_path):
    captured = {}

    def opener(request, timeout):
        captured["request"] = request
        return Response({"content": [{"type": "text", "text": "done"}]})

    provider = ModelProvider(
        config(
            tmp_path,
            provider="anthropic",
            base_url="https://api.anthropic.test",
        ),
        opener=opener,
    )
    text, calls, _raw = provider.complete(
        [
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "inspect"},
        ],
        [],
    )

    assert text == "done"
    assert calls == []
    request = captured["request"]
    assert request.full_url == "https://api.anthropic.test/v1/messages"
    assert request.get_header("X-api-key") == "secret"
    assert json.loads(request.data)["system"] == "policy"


def test_local_tools_reject_workspace_escape_and_mutating_git(tmp_path):
    tools = LocalTools(config(tmp_path))
    with pytest.raises(APIError, match="escapes"):
        tools.execute("read_file", {"path": "../secret"})
    with pytest.raises(APIError, match="Mutating Git"):
        tools.execute("run_command", {"argv": ["git", "reset", "--hard"]})


def test_read_file_stays_inside_workspace(tmp_path):
    (tmp_path / "hello.txt").write_text("first\nsecond\n")
    tools = LocalTools(config(tmp_path))
    assert (
        tools.execute(
            "read_file",
            {"path": "hello.txt", "start_line": 2, "end_line": 2},
        )
        == "2: second"
    )


def test_agent_executes_tool_then_returns_final_answer(tmp_path):
    class FakeProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, schemas):
            self.calls += 1
            if self.calls == 1:
                return (
                    "",
                    [{"id": "1", "name": "read_file", "arguments": {"path": "a.txt"}}],
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"a.txt"}',
                                },
                            }
                        ],
                    },
                )
            assert messages[-1]["role"] == "tool"
            assert "hello" in messages[-1]["content"]
            return "finished", [], {"role": "assistant", "content": "finished"}

    (tmp_path / "a.txt").write_text("hello")
    result = run_local_agent(
        "inspect",
        config(tmp_path),
        provider=FakeProvider(),
        tools=LocalTools(config(tmp_path)),
    )
    assert result == "finished"
