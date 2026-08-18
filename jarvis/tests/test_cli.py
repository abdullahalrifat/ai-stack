import io
import json
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest
from aistack_cli.client import AgentClient, APIError
from aistack_cli.main import (
    build_parser,
    cancellation_signals,
    configure_shell_history,
    follow_run,
    interactive_shell,
    latest_conversation_id,
    main,
    match_workspace,
    read_shell_input,
    request_edit_permission,
    resolve_api_key,
    resolve_project,
    review_run,
    run_exit_code,
    run_task,
    task_requests_edits,
)
from aistack_cli.protocol import PROTOCOL_HEADER, validate_capabilities
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

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

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
        client_id="terminal-1",
    )

    request = captured["request"]
    assert result["run_id"] == "run-1"
    assert request.full_url == "http://agent.test/runs"
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer secret"
    assert (
        next(
            value
            for key, value in request.header_items()
            if key.casefold() == PROTOCOL_HEADER.casefold()
        )
        == "1"
    )
    assert json.loads(request.data) == {
        "protocol_version": 1,
        "task": "Fix the tests",
        "workspace": "/workspace/repo",
        "model": "orchestrator",
        "conversation_id": "conversation",
        "project_id": "project",
        "allow_write": True,
        "client_id": "terminal-1",
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


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timed out"), ConnectionResetError("connection reset")],
)
def test_client_wraps_transport_errors(error):
    def opener(_request, timeout):
        raise error

    client = AgentClient("http://agent.test", "secret", opener=opener)

    with pytest.raises(APIError, match="Request to http://agent.test was interrupted"):
        client.health()


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
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        return response

    client = AgentClient("http://agent.test", "secret", opener=opener)

    events = list(client.stream_events("run id", client_id="terminal-1"))

    assert events == [
        {"id": 1, "event_type": "planning", "payload": {}},
        {"event_type": "stream_closed", "status": "completed"},
    ]
    assert response.closed
    assert captured["url"].endswith(
        "/runs/run%20id/events?after=0&client_id=terminal-1"
    )


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


def test_client_rejects_oversized_sse_event(monkeypatch):
    response = FakeResponse(
        lines=[b'data: {"event_type":"output_delta","payload":{"content":"large"}}\n']
    )
    monkeypatch.setattr("aistack_cli.client.MAX_SSE_EVENT_BYTES", 16)
    client = AgentClient(
        "http://agent.test",
        "secret",
        opener=lambda _request, timeout: response,
    )

    with pytest.raises(APIError, match="safety limit"):
        list(client.stream_events("run-1"))

    assert response.closed is True


def test_client_rejects_oversized_json_response(monkeypatch):
    monkeypatch.setattr("aistack_cli.client.MAX_HTTP_RESPONSE_BYTES", 64)
    client = AgentClient(
        "http://agent.test",
        "secret",
        opener=lambda _request, timeout: FakeResponse(b"x" * 65),
    )

    with pytest.raises(APIError, match="exceeded.*safety limit"):
        client.request("GET", "/runs")


def test_match_workspace_maps_host_checkout_to_container_path():
    choices = ["/workspace", "/workspace/ai-stack", "/workspace/other"]

    assert (
        match_workspace(Path("/mnt/work/code/ai-stack"), choices)
        == "/workspace/ai-stack"
    )
    assert match_workspace(Path("/tmp/unrelated"), choices) is None


def test_latest_conversation_is_scoped_to_workspace():
    class FakeClient:
        def list_runs(self, limit):
            assert limit == 100
            return [
                {
                    "requested_workspace": "/workspace/other",
                    "conversation_id": "other",
                    "status": "completed",
                },
                {
                    "requested_workspace": "/workspace/repo",
                    "conversation_id": "latest-local",
                    "status": "completed",
                },
                {
                    "requested_workspace": "/workspace/repo",
                    "conversation_id": "older-local",
                    "status": "completed",
                },
            ]

    assert latest_conversation_id(FakeClient(), "/workspace/repo") == "latest-local"


def test_latest_conversation_reports_empty_workspace_history():
    class FakeClient:
        def list_runs(self, limit):
            return []

    with pytest.raises(APIError, match="No previous conversation"):
        latest_conversation_id(FakeClient(), "/workspace/repo")


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


def test_shell_input_supports_explicit_multiline_tasks(monkeypatch):
    lines = iter(["review these files\\", "and run the tests"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(lines))

    assert read_shell_input("prompt> ") == "review these files\nand run the tests"


def test_shell_history_is_persistent_and_completes_commands(monkeypatch, tmp_path):
    calls = {}

    class FakeReadline:
        def read_history_file(self, path):
            calls["read"] = Path(path)
            raise FileNotFoundError

        def set_history_length(self, length):
            calls["length"] = length

        def set_completer(self, completer):
            calls["completer"] = completer

        def parse_and_bind(self, binding):
            calls["binding"] = binding

        def write_history_file(self, path):
            calls["write"] = Path(path)

    callbacks = []
    history_file = tmp_path / "state" / "history"
    monkeypatch.setenv("AISTACK_HISTORY_FILE", str(history_file))
    monkeypatch.setitem(sys.modules, "readline", FakeReadline())
    monkeypatch.setattr("aistack_cli.main.atexit.register", callbacks.append)

    configure_shell_history()

    assert calls["read"] == history_file
    assert calls["length"] == 1_000
    assert calls["binding"] == "tab: complete"
    assert calls["completer"]("/fore", 0) == "/foreground"
    assert calls["completer"]("/fore", 1) is None
    callbacks[0]()
    assert calls["write"] == history_file


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


def test_renderer_does_not_repeat_terminal_failure():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)

    renderer.render(
        {
            "event_type": "run_failed",
            "payload": {"error": "Request timed out."},
        }
    )

    renderer.render_run({"status": "failed", "error": "Request timed out."})

    assert output.getvalue() == "Run failed: Request timed out.\n"


def test_renderer_sanitizes_untrusted_terminal_control_sequences():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)

    renderer.render(
        {
            "event_type": "output_delta",
            "payload": {"content": "safe\x1b[2Jstill-safe\x00"},
        }
    )

    assert output.getvalue() == "safe\\x1b[2Jstill-safe\\x00"


def test_renderer_preserves_only_its_own_color_sequences():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=True, verbose=True)

    renderer.render({"event_type": "queued", "payload": {}})
    renderer.render(
        {
            "event_type": "tool_call",
            "payload": {
                "tool": "run_tests\x1b[2J",
                "args": {"directory": ".\x1b[31m"},
            },
        }
    )

    assert output.getvalue() == (
        "\x1b[2m• Working…\x1b[0m\n"
        "\x1b[33m→ run_tests\\x1b[2J\x1b[0m"
        ' {"directory": ".\\u001b[31m"}\n'
    )


def test_renderer_reports_only_meaningful_lifecycle_stage_changes():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)

    for event_type in (
        "queued",
        "run_started",
        "sandbox_creating",
        "sandbox_ready",
        "planning",
        "plan_ready",
        "step_started",
    ):
        renderer.render({"event_type": event_type, "payload": {}})

    assert output.getvalue() == (
        "• Working…\n"
        "• Preparing sandbox…\n"
        "• Planning…\n"
        "• Inspecting codebase…\n"
        "• Thinking…\n"
    )


def test_renderer_hides_automatic_prefetch_details():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)

    renderer.render(
        {
            "event_type": "tool_call",
            "payload": {"tool": "list_files", "args": {}, "prefetch": True},
        }
    )
    renderer.render(
        {
            "event_type": "tool_result",
            "payload": {"tool": "list_files", "result": [], "prefetch": True},
        }
    )

    assert output.getvalue() == ""


def test_renderer_shows_prefetched_coverage_as_a_progress_stage():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)

    renderer.render(
        {
            "event_type": "tool_call",
            "payload": {
                "tool": "run_tests",
                "args": {"kind": "pytest_coverage"},
                "prefetch": True,
            },
        }
    )

    assert output.getvalue() == "• Running tests…\n"


def test_renderer_collapses_duplicate_failures_and_marks_partial_run():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False)
    failure = {
        "event_type": "tool_result",
        "payload": {"tool": "tree", "result": {"error": "bad path"}},
    }

    renderer.render(failure)
    renderer.render(failure)
    renderer.render(
        {
            "event_type": "run_completed",
            "payload": {"answer": "Incomplete", "partial": True},
        }
    )

    assert output.getvalue().count("bad path") == 1
    assert "Run ended incomplete" in output.getvalue()


def test_renderer_keeps_stream_json_as_one_valid_object_per_line():
    output = io.StringIO()
    renderer = EventRenderer(stream=output, output="stream-json", color=False)
    event = {
        "event_type": "output_delta",
        "payload": {"content": "line one\nline two\x1b"},
    }

    renderer.render(event)

    assert json.loads(output.getvalue()) == event
    assert output.getvalue().count("\n") == 1


@pytest.mark.parametrize(
    "event_type",
    [
        "answer_audit_failed",
        "client_disconnected",
        "context_budgeted",
        "diff_ready",
        "final_answer",
        "max_steps_reached",
        "output_delta",
        "plan_ready",
        "planning",
        "queued",
        "run_cancelled",
        "run_cancelling",
        "run_completed",
        "run_discarded",
        "run_failed",
        "run_merged",
        "run_started",
        "sandbox_creating",
        "sandbox_ready",
        "step_started",
        "tool_call",
        "tool_kill_failed",
        "tool_result",
        "tool_timed_out",
        "unproductive_tool_loop",
    ],
)
def test_stream_json_golden_contract_covers_every_durable_event(event_type):
    output = io.StringIO()
    renderer = EventRenderer(stream=output, output="stream-json", color=False)
    event = {
        "protocol_version": 1,
        "schema_version": 1,
        "id": 7,
        "event_type": event_type,
        "payload": {"content": "αβ", "future": True},
    }

    renderer.render(event)

    assert json.loads(output.getvalue()) == event
    assert output.getvalue().endswith("\n")


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"event_type": "queued", "payload": {}}, "• Working…\n"),
        ({"event_type": "run_started", "payload": {}}, "• Working…\n"),
        ({"event_type": "planning", "payload": {}}, "• Planning…\n"),
        (
            {"event_type": "plan_ready", "payload": {"plan": ["inspect"]}},
            "• Inspecting codebase…\n",
        ),
        (
            {"event_type": "step_started", "payload": {"step": 2}},
            "• Thinking…\n",
        ),
        (
            {
                "event_type": "tool_call",
                "payload": {"tool": "tree", "args": {"depth": 2}},
            },
            "• Inspecting codebase…\n",
        ),
        (
            {
                "event_type": "tool_result",
                "payload": {"tool": "tree", "result": "README.md"},
            },
            "",
        ),
        (
            {
                "event_type": "tool_result",
                "payload": {"tool": "tree", "result": {"error": "not found"}},
            },
            '← tree {"error": "not found"}\n',
        ),
        (
            {"event_type": "run_failed", "payload": {"error": "boom"}},
            "Run failed: boom\n",
        ),
        (
            {"event_type": "run_cancelled", "payload": {}},
            "Run cancelled.\n",
        ),
    ],
)
def test_text_event_goldens(event, expected):
    output = io.StringIO()
    EventRenderer(stream=output, color=False).render(event)

    assert output.getvalue() == expected


def test_sse_invalid_utf8_is_replaced_without_breaking_json():
    response = FakeResponse(
        lines=[
            b'data: {"event_type":"output_delta","payload":{"content":"\xff"}}\n',
            b"\n",
        ]
    )
    client = AgentClient(
        "http://agent.test",
        "secret",
        opener=lambda _request, timeout: response,
    )

    event = list(client.stream_events("run-1"))[0]

    assert event["payload"]["content"] == "\ufffd"


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


def test_follow_run_resets_retry_budget_after_progress(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.stream_calls = 0

        def stream_events(self, run_id, after=0):
            self.stream_calls += 1
            yield {
                "id": self.stream_calls,
                "event_type": "planning",
                "payload": {},
            }

        def get_run(self, run_id):
            status = "completed" if self.stream_calls == 8 else "running"
            return {"id": run_id, "status": status, "answer": "Done"}

        def action(self, run_id, action):
            raise AssertionError("cancel should not be called")

    monkeypatch.setattr("aistack_cli.main.time.sleep", lambda seconds: None)
    client = FakeClient()

    run = follow_run(
        client,
        "run-1",
        EventRenderer(stream=io.StringIO(), color=False),
    )

    assert run["status"] == "completed"
    assert client.stream_calls == 8


def test_follow_run_cancels_foreground_run_after_unrecovered_error(monkeypatch):
    actions = []

    class FakeClient:
        def stream_events(self, run_id, **options):
            assert options["client_id"] == "terminal-1"
            raise APIError("connection lost")

        def action(self, run_id, action):
            actions.append((run_id, action))
            return {"status": "cancelling"}

    monkeypatch.setattr("aistack_cli.main.time.sleep", lambda seconds: None)

    with pytest.raises(APIError, match="connection lost"):
        follow_run(
            FakeClient(),
            "run-1",
            EventRenderer(stream=io.StringIO(), color=False),
            client_id="terminal-1",
        )

    assert actions == [("run-1", "cancel")]


def test_follow_run_cancels_immediately_on_keyboard_interrupt():
    actions = []

    class FakeClient:
        def stream_events(self, run_id, **options):
            raise KeyboardInterrupt

        def action(self, run_id, action):
            actions.append((run_id, action))
            return {"status": "cancelling"}

    with pytest.raises(KeyboardInterrupt):
        follow_run(
            FakeClient(),
            "run-1",
            EventRenderer(stream=io.StringIO(), color=False),
            client_id="terminal-1",
        )

    assert actions == [("run-1", "cancel")]


@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGHUP"])
def test_shutdown_signals_enter_normal_keyboard_cancellation_path(signal_name):
    import signal

    signum = getattr(signal, signal_name)
    previous = signal.getsignal(signum)
    with cancellation_signals():
        handler = signal.getsignal(signum)
        with pytest.raises(KeyboardInterrupt):
            handler(signum, None)

    assert signal.getsignal(signum) == previous


def test_review_auto_applies_pending_changes(monkeypatch):
    calls = []

    class FakeClient:
        def action(self, run_id, action):
            calls.append((run_id, action))
            return {"run_id": run_id, "status": "completed"}

    run = {"id": "run-1", "status": "awaiting_approval"}

    changed = review_run(FakeClient(), run, auto_approve=True)

    assert changed["status"] == "completed"
    assert calls == [("run-1", "approve")]


def test_foreground_run_rejects_server_without_client_lease_support():
    actions = []

    class FakeClient:
        def create_run(self, *args, **kwargs):
            assert kwargs["client_id"]
            return {"run_id": "run-1", "status": "queued"}

        def action(self, run_id, action):
            actions.append((run_id, action))
            return {"status": "cancelled"}

    with pytest.raises(APIError, match="does not support foreground client leases"):
        run_task(
            FakeClient(),
            "review the repository",
            workspace="/workspace/example",
            project_id=None,
            conversation_id="conversation",
            allow_write=False,
            detached=False,
            output="text",
            review=False,
        )

    assert actions == [("run-1", "cancel")]


def test_protocol_range_mismatch_has_actionable_upgrade_error():
    with pytest.raises(Exception, match="Upgrade the CLI or server"):
        validate_capabilities(
            {
                "api_version": "2",
                "protocol": {"current": 2, "min_cli": 2, "max_cli": 3},
                "features": [],
            }
        )


def test_machine_output_never_opens_review_prompt(monkeypatch, capsys):
    class FakeClient:
        def ensure_compatible(self, *features):
            assert features == ("durable_events", "client_leases")

        def create_run(self, *args, **kwargs):
            return {
                "run_id": "run-1",
                "status": "queued",
                "client_id": kwargs["client_id"],
            }

        def stream_events(self, run_id, **options):
            yield {"event_type": "stream_closed", "status": "awaiting_approval"}

        def get_run(self, run_id):
            return {
                "id": run_id,
                "status": "awaiting_approval",
                "answer": "review me",
            }

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(
            AssertionError("machine output must not prompt")
        ),
    )

    run = run_task(
        FakeClient(),
        "change code",
        workspace="/workspace/example",
        project_id=None,
        conversation_id="conversation",
        allow_write=True,
        detached=False,
        output="json",
        review=True,
    )

    assert run["status"] == "awaiting_approval"
    assert json.loads(capsys.readouterr().out)["status"] == "awaiting_approval"


def test_write_flag_works_before_or_after_run_subcommand():
    parser = build_parser()

    assert parser.parse_args(["--write", "run", "task"]).write is True
    assert parser.parse_args(["run", "--write", "task"]).write is True
    assert parser.parse_args(["run", "--allow-edits", "task"]).write is True
    assert parser.parse_args(["run", "task"]).write is True
    assert parser.parse_args(["--read-only", "run", "task"]).write is False
    assert parser.parse_args(["run", "--read-only", "task"]).write is False


def test_interactive_edit_permission_is_scoped_to_one_task(monkeypatch, capsys):
    answers = iter(["maybe", "yes", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert request_edit_permission() is True
    assert request_edit_permission() is False
    assert "Please answer yes or no." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("review this repository", False),
        ("summarize README.md", False),
        ("fix the failing tests", True),
        ("improve test coverage", True),
        ("imporove test coverage", True),
    ],
)
def test_edit_prompt_is_limited_to_change_requests(task, expected):
    assert task_requests_edits(task) is expected


def test_detach_flag_works_before_or_after_run_subcommand():
    parser = build_parser()

    assert parser.parse_args(["--detach", "run", "task"]).detach is True
    assert parser.parse_args(["run", "--detach", "task"]).detach is True


def test_continue_flag_works_before_or_after_run_subcommand():
    parser = build_parser()

    assert parser.parse_args(["--continue", "run", "task"]).continue_session is True
    assert parser.parse_args(["run", "--continue", "task"]).continue_session is True


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

        def capabilities(self):
            return {"api_version": "1", "features": ["client_leases"]}

    monkeypatch.setenv("AISTACK_API_KEY", "secret")
    monkeypatch.setattr("aistack_cli.main.AgentClient", FakeClient)

    assert main(["--url", "http://agent.test", "doctor"]) == 0
    assert capsys.readouterr().out == (
        "API: ok (http://agent.test)\n"
        "Protocol: 1\n"
        "Features: client_leases\n"
        "Workspace: /workspace\n"
        "Authentication: ok\n"
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


def test_main_shorthand_automatically_allows_requested_edit(monkeypatch, tmp_path):
    calls = []

    class FakeClient:
        def __init__(self, _base_url, _api_key):
            pass

        def workspaces(self):
            return ["/workspace/example"]

    def fake_run_task(client, task, **options):
        calls.append(options)
        return {"status": "completed"}

    checkout = tmp_path / "example"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("AISTACK_API_KEY", "secret")
    monkeypatch.setattr("aistack_cli.main.AgentClient", FakeClient)
    monkeypatch.setattr("aistack_cli.main.run_task", fake_run_task)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(
            AssertionError("edit tasks must not prompt for permission")
        ),
    )

    assert main(["fix", "the tests"]) == 0
    assert calls[0]["allow_write"] is True


def test_main_local_stream_simulate_mode(capsys):
    assert main(["stream", "Explain", "quicksort", "briefly.", "--simulate"]) == 0
    output = capsys.readouterr()
    assert "Quicksort is a divide-and-conquer sorting algorithm" in output.out
    assert "[done in" in output.err


def test_main_local_stream_fails_when_runtime_is_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(
        "aistack_cli.main._import_chat_stream_text",
        lambda: None,
    )

    assert main(["stream", "never", "fabricate", "this"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Use 'aistack run'" in output.err
    assert "Quicksort" not in output.err


def test_main_reports_configuration_errors_without_traceback(monkeypatch, capsys):
    monkeypatch.delenv("AISTACK_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_API_KEY", raising=False)
    monkeypatch.delenv("AISTACK_ENV_FILE", raising=False)

    assert main(["doctor"]) == 1
    assert "No API key configured" in capsys.readouterr().err


def test_main_handles_broken_pipe_without_traceback(monkeypatch):
    class BrokenOutput:
        def write(self, _value):
            raise BrokenPipeError

        def flush(self):
            pass

        def fileno(self):
            raise OSError("not a real descriptor")

    class FakeClient:
        def __init__(self, _base_url, _api_key):
            pass

        def list_runs(self, _limit):
            return [{"id": "run-1", "status": "completed", "task": "done"}]

    monkeypatch.setenv("AISTACK_API_KEY", "secret")
    monkeypatch.setattr("aistack_cli.main.AgentClient", FakeClient)
    monkeypatch.setattr(sys, "stdout", BrokenOutput())

    assert main(["list"]) == 0


def test_interactive_shell_automatically_allows_edit_tasks(monkeypatch, capsys):
    tasks = []
    commands = iter(
        [
            "fix the tests",
            "/detach",
            "/status",
            "/foreground",
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
            allow_write=True,
        )
        == 0
    )
    assert tasks[0][1] == "fix the tests"
    assert tasks[0][2]["allow_write"] is True
    output = capsys.readouterr().out
    assert "edit permission: automatic for change requests" in output
    assert "lifecycle: detached" in output
    assert "Foreground mode enabled" in output
    assert "run-123" in output


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        ("tool_call", {"tool": "pytest", "args": {"path": "tests"}}, "pytest"),
        ("tool_result", {"tool": "pytest", "result": "18 passed"}, "18 passed"),
        ("run_failed", {"error": "boom"}, "Run failed: boom"),
        ("run_cancelled", {}, "Run cancelled."),
        ("sandbox_ready", {}, "Planning"),
    ],
)
def test_renderer_covers_status_and_tool_events(event_type, payload, expected):
    output = io.StringIO()
    renderer = EventRenderer(stream=output, color=False, verbose=True)

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
