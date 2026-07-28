import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from aistack_cli.client import AgentClient, APIError
from aistack_cli.main import (
    build_parser,
    follow_run,
    interactive_shell,
    main,
    match_workspace,
    resolve_api_key,
    resolve_project,
    review_run,
    run_exit_code,
)
from aistack_cli.render import EventRenderer


class FakeResponse:
    def __init__(self, body=b"", lines=None):
        self.body = body
        self.lines = lines or []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __iter__(self):
        return iter(self.lines)

    def read(self):
        return self.body

    def close(self):
        self.closed = True


def test_client_posts_authenticated_run_request():
    captured = {}

    def opener(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(b'{"run_id":"run-1","status":"queued"}')

    client = AgentClient("http://agent.test/", "secret", opener=opener)
    result = client.create_run(
        "Fix the tests",
        workspace="/workspace/repo",
        conversation_id="conversation",
        allow_write=True,
        project_id="project",
    )

    request = captured["request"]
    assert result["run_id"] == "run-1"
    assert request.full_url == "http://agent.test/runs"
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer secret"
    assert json.loads(request.data) == {
        "task": "Fix the tests",
        "workspace": "/workspace/repo",
        "model": "orchestrator",
        "conversation_id": "conversation",
        "project_id": "project",
        "allow_write": True,
    }


def test_client_surfaces_api_error_detail():
    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"detail":"workspace is invalid"}'),
        )

    client = AgentClient("http://agent.test", "secret", opener=opener)

    with pytest.raises(APIError, match="400 workspace is invalid"):
        client.workspaces()


def test_client_parses_sse_and_ignores_comments():
    response = FakeResponse(
        lines=[
            b": heartbeat\n",
            b"\n",
            b'data: {"id":1,"event_type":"planning","payload":{}}\n',
            b"\n",
            b'data: {"event_type":"stream_closed",\n',
            b'data: "status":"completed"}\n',
            b"\n",
        ]
    )
    client = AgentClient(
        "http://agent.test",
        "secret",
        opener=lambda request, timeout: response,
    )

    events = list(client.stream_events("run id"))

    assert events == [
        {"id": 1, "event_type": "planning", "payload": {}},
        {"event_type": "stream_closed", "status": "completed"},
    ]
    assert response.closed


def test_client_converts_stream_socket_timeout_to_reconnectable_error():
    class TimeoutResponse(FakeResponse):
        def __iter__(self):
            yield b'data: {"id":1,"event_type":"planning","payload":{}}\n'
            yield b"\n"
            raise TimeoutError("timed out")

    response = TimeoutResponse()
    captured = {}

    def opener(request, timeout):
        captured["timeout"] = timeout
        return response

    client = AgentClient(
        "http://agent.test",
        "secret",
        stream_timeout=75,
        opener=opener,
    )

    events = client.stream_events("run-1")
    assert next(events)["id"] == 1
    with pytest.raises(APIError, match="stream was interrupted.*timed out"):
        next(events)
    assert captured["timeout"] == 75
    assert response.closed


def test_match_workspace_maps_host_checkout_to_container_path():
    choices = ["/workspace", "/workspace/ai-stack", "/workspace/other"]

    assert (
        match_workspace(Path("/mnt/work/code/ai-stack"), choices)
        == "/workspace/ai-stack"
    )
    assert match_workspace(Path("/tmp/unrelated"), choices) is None


def test_project_selector_accepts_name_or_unique_id_prefix():
    projects = [
        {"id": "abc-123", "name": "ai-stack", "workspace": "/workspace/ai-stack"},
        {"id": "def-456", "name": "other", "workspace": "/workspace/other"},
    ]

    assert resolve_project(projects, "ai-stack")["id"] == "abc-123"
    assert resolve_project(projects, "def")["name"] == "other"
    with pytest.raises(APIError, match="missing or ambiguous"):
        resolve_project(projects, "missing")


def test_api_key_can_be_read_from_non_executable_env_file(
    monkeypatch,
    tmp_path,
):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=value\nAGENT_API_KEY='local-secret'\n")
    monkeypatch.delenv("AISTACK_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.setenv("AISTACK_ENV_FILE", str(env_file))

    assert resolve_api_key() == "local-secret"


def test_renderer_does_not_repeat_final_answer():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)

    renderer.render(
        {
            "event_type": "run_completed",
            "payload": {"answer": "Finished.", "has_pending_diff": False},
        }
    )
    renderer.render_run({"status": "completed", "answer": "Finished."})

    assert output.getvalue() == "Finished.\n"


def test_follow_run_reconnects_from_last_durable_event(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.stream_calls = []
            self.status_calls = 0

        def stream_events(self, run_id, after=0):
            self.stream_calls.append(after)
            if len(self.stream_calls) == 1:
                yield {
                    "id": 7,
                    "event_type": "output_delta",
                    "payload": {"content": "Done"},
                }
                return
            yield {
                "id": 8,
                "event_type": "run_completed",
                "payload": {"answer": "Done", "has_pending_diff": False},
            }
            yield {"event_type": "stream_closed", "status": "completed"}

        def get_run(self, run_id):
            self.status_calls += 1
            return {
                "id": run_id,
                "status": "running" if self.status_calls == 1 else "completed",
                "answer": "Done",
            }

        def action(self, run_id, action):
            raise AssertionError("cancel should not be called")

    monkeypatch.setattr("aistack_cli.main.time.sleep", lambda seconds: None)
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)
    client = FakeClient()

    run = follow_run(client, "run-1", renderer)

    assert run["status"] == "completed"
    assert client.stream_calls == [0, 7]
    assert output.getvalue().count("Done") == 1


def test_follow_run_reconnects_after_stream_timeout(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.stream_calls = []

        def stream_events(self, run_id, after=0):
            self.stream_calls.append(after)
            if len(self.stream_calls) == 1:
                yield {
                    "id": 11,
                    "event_type": "planning",
                    "payload": {},
                }
                raise APIError("Run event stream was interrupted: timed out")
            yield {
                "id": 12,
                "event_type": "run_completed",
                "payload": {"answer": "Recovered.", "has_pending_diff": False},
            }
            yield {"event_type": "stream_closed", "status": "completed"}

        def get_run(self, run_id):
            return {"id": run_id, "status": "completed", "answer": "Recovered."}

        def action(self, run_id, action):
            raise AssertionError("cancel should not be called")

    monkeypatch.setattr("aistack_cli.main.time.sleep", lambda seconds: None)
    output = io.StringIO()
    client = FakeClient()

    run = follow_run(
        client,
        "run-1",
        EventRenderer(stream=output, color=False),
    )

    assert run["status"] == "completed"
    assert client.stream_calls == [0, 11]
    assert output.getvalue().count("Recovered.") == 1


def test_review_requires_explicit_approval(monkeypatch):
    calls = []

    class FakeClient:
        def action(self, run_id, action):
            calls.append((run_id, action))
            return {"run_id": run_id, "status": "completed"}

    monkeypatch.setattr("builtins.input", lambda prompt: "approve")
    run = {"id": "run-1", "status": "awaiting_approval"}

    changed = review_run(FakeClient(), run, interactive=True)

    assert changed["status"] == "completed"
    assert calls == [("run-1", "approve")]


def test_write_flag_works_before_or_after_run_subcommand():
    parser = build_parser()

    assert parser.parse_args(["--write", "run", "task"]).write is True
    assert parser.parse_args(["run", "--write", "task"]).write is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("completed", 0),
        ("awaiting_approval", 0),
        ("failed", 1),
        ("cancelled", 130),
    ],
)
def test_run_exit_codes_are_script_friendly(status, expected):
    assert run_exit_code({"status": status}) == expected


def test_main_doctor_checks_api_and_resolves_server_default(monkeypatch, capsys):
    class FakeClient:
        def __init__(self, base_url, api_key):
            assert base_url == "http://agent.test"
            assert api_key == "secret"

        def workspaces(self):
            return ["/workspace/other"]

        def default_workspace(self):
            return "/workspace"

        def health(self):
            return {"status": "ok"}

    monkeypatch.setenv("AISTACK_API_KEY", "secret")
    monkeypatch.setattr("aistack_cli.main.AgentClient", FakeClient)

    assert main(["--url", "http://agent.test", "doctor"]) == 0
    assert capsys.readouterr().out == (
        "API: ok (http://agent.test)\n" "Workspace: /workspace\n" "Authentication: ok\n"
    )


def test_main_shorthand_starts_run_with_mapped_workspace(
    monkeypatch,
    capsys,
    tmp_path,
):
    calls = []

    class FakeClient:
        def __init__(self, _base_url, _api_key):
            pass

        def workspaces(self):
            return ["/workspace/example"]

    def fake_run_task(client, task, **options):
        calls.append((client, task, options))
        return {"status": "completed"}

    checkout = tmp_path / "example"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("AISTACK_API_KEY", "secret")
    monkeypatch.setattr("aistack_cli.main.AgentClient", FakeClient)
    monkeypatch.setattr("aistack_cli.main.run_task", fake_run_task)

    assert main(["review", "the repo"]) == 0
    assert calls[0][1] == "review the repo"
    assert calls[0][2]["workspace"] == "/workspace/example"
    assert calls[0][2]["allow_write"] is False
    assert capsys.readouterr().err == ""


def test_main_reports_configuration_errors_without_traceback(monkeypatch, capsys):
    monkeypatch.delenv("AISTACK_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.delenv("AISTACK_ENV_FILE", raising=False)

    assert main(["doctor"]) == 1
    assert "No API key configured" in capsys.readouterr().err


def test_interactive_shell_changes_modes_and_runs_tasks(monkeypatch, capsys):
    tasks = []
    commands = iter(
        [
            "/write",
            "fix the tests",
            "/read-only",
            "/new",
            "/runs",
            "/exit",
        ]
    )

    class FakeClient:
        def list_runs(self):
            return [
                {
                    "id": "run-123456",
                    "status": "completed",
                    "created_at": "2026-07-28T10:00:00Z",
                    "task": "fix the tests",
                }
            ]

    def fake_run_task(client, task, **options):
        tasks.append((client, task, options))
        return {
            "id": "run-123456",
            "status": "completed",
            "conversation_id": options["conversation_id"],
        }

    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))
    monkeypatch.setattr("aistack_cli.main.run_task", fake_run_task)

    assert (
        interactive_shell(
            FakeClient(),
            workspace="/workspace/example",
            project_id=None,
            allow_write=False,
        )
        == 0
    )
    assert tasks[0][1] == "fix the tests"
    assert tasks[0][2]["allow_write"] is True
    output = capsys.readouterr().out
    assert "Reviewed sandbox writes enabled." in output
    assert "Read-only mode enabled." in output
    assert "run-123" in output


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        ("tool_call", {"tool": "pytest", "args": {"path": "tests"}}, "pytest"),
        ("tool_result", {"tool": "pytest", "result": "18 passed"}, "18 passed"),
        ("run_failed", {"error": "boom"}, "Run failed: boom"),
        ("run_cancelled", {}, "Run cancelled."),
        ("sandbox_ready", {}, "sandbox ready"),
    ],
)
def test_renderer_covers_status_and_tool_events(event_type, payload, expected):
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)

    renderer.render({"event_type": event_type, "payload": payload})

    assert expected in output.getvalue()


def test_renderer_supports_json_and_stream_json_outputs():
    stream = io.StringIO()
    EventRenderer(output="stream-json", stream=stream).render(
        {"event_type": "planning", "payload": {}}
    )
    assert json.loads(stream.getvalue())["event_type"] == "planning"

    final = io.StringIO()
    EventRenderer(output="json", stream=final).render_run(
        {"id": "run-1", "status": "completed"}
    )
    assert json.loads(final.getvalue())["status"] == "completed"
