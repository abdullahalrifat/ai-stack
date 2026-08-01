from pathlib import Path

import pytest

from app.core.cancellation import cancellation_context
from app.core.exceptions import RunCancelled
from app.tools import filesystem


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(filesystem, "WORKSPACE_ROOTS", [tmp_path])
    return tmp_path


def test_workspace_rejects_paths_outside_configured_root(workspace, tmp_path):
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)

    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(outside))


def test_workspace_rejects_symlink_escape(workspace, tmp_path):
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    escaped = workspace / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(escaped))


def test_internal_workspace_accepts_only_direct_git_sandbox(
    workspace, tmp_path, monkeypatch
):
    sandbox_root = tmp_path.parent / "sandboxes"
    valid = sandbox_root / "run-1"
    nested = sandbox_root / "nested" / "run-2"
    arbitrary = sandbox_root / "arbitrary"
    (valid / ".git").mkdir(parents=True)
    (nested / ".git").mkdir(parents=True)
    arbitrary.mkdir(parents=True)
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", sandbox_root)

    assert filesystem.validate_workspace(
        str(valid), allow_sandbox=True
    ) == valid.resolve()
    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(valid))
    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(nested), allow_sandbox=True)
    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(arbitrary), allow_sandbox=True)


def test_request_workspace_uses_explicit_repository_in_prompt(workspace, monkeypatch):
    repository = workspace / "ai-stack"
    repository.mkdir()
    monkeypatch.setattr(filesystem, "DEFAULT_WORKSPACE", workspace)

    selected = filesystem.resolve_request_workspace(
        str(workspace), f"Review the repository at {repository}."
    )

    assert selected == str(repository)


def test_explicit_workspace_field_takes_precedence_over_prompt(workspace, monkeypatch):
    repository = workspace / "ai-stack"
    other_repository = workspace / "other"
    repository.mkdir()
    other_repository.mkdir()
    monkeypatch.setattr(filesystem, "DEFAULT_WORKSPACE", workspace)

    selected = filesystem.resolve_request_workspace(
        str(other_repository), f"Review {repository}."
    )

    assert selected == str(other_repository)


def test_resolve_path_accepts_absolute_workspace_without_leading_slash(workspace):
    repository = workspace / "ai-stack"
    repository.mkdir()
    missing_leading_slash = str(repository).lstrip("/")

    with filesystem.workspace_context(str(repository)):
        assert filesystem.resolve_path(missing_leading_slash) == repository


def test_tree_skips_runtime_state_directories(workspace):
    (workspace / "postgres").mkdir()
    (workspace / "open-webui" / "uploads").mkdir(parents=True)
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("print('ok')")

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.tree.invoke({"directory": ".", "depth": 2})

    assert "postgres" not in result
    assert "open-webui" not in result
    assert "src" in result


def test_read_tools_reject_binary_files(workspace):
    binary = workspace / "upload.pdf"
    binary.write_bytes(b"%PDF-1.7\x00binary")

    with filesystem.workspace_context(str(workspace)):
        single = filesystem.read_file.invoke({"file_path": "upload.pdf"})
        batch = filesystem.inspect_files.invoke({"paths": ["upload.pdf"]})

    assert single == {"error": "File is binary, not UTF-8 text."}
    assert batch["items"] == [
        {"path": "upload.pdf", "error": "File is binary, not UTF-8 text."}
    ]


def test_repository_tools_hide_secrets_but_allow_env_templates(workspace):
    (workspace / ".env").write_text("SECRET=do-not-read")
    (workspace / ".env.local").write_text("SECRET=also-hidden")
    (workspace / ".env.example").write_text("SECRET=placeholder")
    (workspace / "private.pem").write_text("key")

    with filesystem.workspace_context(str(workspace)):
        listing = filesystem.list_files.invoke({"directory": "."})
        denied = filesystem.read_file.invoke({"file_path": ".env"})
        template = filesystem.read_file.invoke({"file_path": ".env.example"})

    names = {item["name"] for item in listing}
    assert ".env" not in names
    assert ".env.local" not in names
    assert "private.pem" not in names
    assert ".env.example" in names
    assert denied == {"error": "Reading sensitive files is not allowed."}
    assert template == "SECRET=placeholder"


def test_inspect_files_bounds_requested_paths(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_INSPECT_PATHS", 2)
    for index in range(3):
        (workspace / f"file-{index}.txt").write_text(str(index))

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.inspect_files.invoke(
            {"paths": ["file-0.txt", "file-1.txt", "file-2.txt"]}
        )

    assert result["truncated"] is True
    assert [item["path"] for item in result["items"]] == ["file-0.txt", "file-1.txt"]


def test_inspect_files_balances_content_across_large_files(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_TOOL_OUTPUT_CHARS", 2_600)
    (workspace / "first.md").write_text("A" * 4_000)
    (workspace / "second.md").write_text("B" * 4_000)

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.inspect_files.invoke({"paths": ["first.md", "second.md"]})

    assert len(result["items"][0]["content"]) == 550
    assert len(result["items"][1]["content"]) == 550
    assert result["items"][1]["content"].startswith("B")


def test_write_edit_and_read_stay_inside_workspace(workspace):
    with filesystem.workspace_context(str(workspace)):
        assert (
            filesystem.write_file.invoke(
                {"file_path": "src/example.txt", "content": "before"}
            )["status"]
            == "written"
        )
        assert (
            filesystem.edit_file.invoke(
                {
                    "file_path": "src/example.txt",
                    "old_string": "before",
                    "new_string": "after",
                }
            )["status"]
            == "edited"
        )
        assert filesystem.read_file.invoke({"file_path": "src/example.txt"}) == "after"
        assert (
            "Access outside workspace denied"
            in filesystem.write_file.invoke(
                {"file_path": "../outside.txt", "content": "nope"}
            )["error"]
        )


def test_run_command_enforces_policy_before_execution(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "ALLOWED_COMMANDS", ["echo"])
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda command, cwd: {"command": command, "exit_code": 0, "output": "ok"},
    )

    with filesystem.workspace_context(str(workspace)):
        assert (
            "not permitted"
            in filesystem.run_command.invoke({"command": "echo ok; echo unsafe"})[
                "error"
            ]
        )
        assert (
            "not an approved command"
            in filesystem.run_command.invoke({"command": "git status"})["error"]
        )
        result = filesystem.run_command.invoke({"command": "echo ok"})

    assert result["exit_code"] == 0
    assert result["output"] == "ok"


def test_inspect_test_environment_reports_stable_coverage_capability(
    workspace, monkeypatch
):
    (workspace / "pytest.ini").write_text("[pytest]\n")
    (workspace / "app").mkdir()
    monkeypatch.setattr(filesystem.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        filesystem.importlib.util,
        "find_spec",
        lambda name: object() if name == "pytest_cov" else None,
    )

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.inspect_test_environment.invoke({"directory": "."})

    assert result["configs"] == ["pytest.ini"]
    assert result["runner"]["pytest_cov"] is True
    assert result["recommended"]["coverage_target"] == "app"
    assert result["fresh_shell_per_command"] is True


def test_run_tests_coverage_uses_explicit_package_target(workspace, monkeypatch):
    commands = []
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda command, cwd: (
            commands.append(command)
            or {"command": command, "exit_code": 0, "output": "covered"}
        ),
    )

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke(
            {
                "kind": "pytest_coverage",
                "directory": ".",
                "coverage_target": "app",
            }
        )

    assert result["exit_code"] == 0
    assert commands == ["pytest -q --cov=app --cov-report=term-missing"]


def test_run_tests_rejects_unsafe_coverage_target(workspace):
    with filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke(
            {
                "kind": "pytest_coverage",
                "coverage_target": "app; touch unsafe",
            }
        )

    assert "dotted Python package" in result["error"]


def test_isolated_runner_job_is_cancelled_with_owning_run(workspace, monkeypatch):
    class Response:
        ok = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    cancellations = []
    checks = iter([False, True])
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(
        filesystem.requests,
        "post",
        lambda url, **kwargs: (
            cancellations.append(url)
            or Response({"job_id": "job-1", "status": "cancelling"})
            if url.endswith("/cancel")
            else Response({"job_id": "job-1", "status": "running"})
        ),
    )
    monkeypatch.setattr(
        filesystem.requests,
        "get",
        lambda *_args, **_kwargs: Response({"job_id": "job-1", "status": "running"}),
    )
    monkeypatch.setattr(filesystem.time, "sleep", lambda _seconds: None)

    with (
        cancellation_context(lambda: next(checks)),
        pytest.raises(RunCancelled),
    ):
        filesystem._run_in_isolated_runner("pytest -q", workspace)

    assert cancellations[-1].endswith("/jobs/job-1/cancel")


def test_isolated_runner_attempts_cleanup_after_network_loss(workspace, monkeypatch):
    class Response:
        ok = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    posts = []

    def post(url, **_kwargs):
        posts.append(url)
        return Response(
            {"job_id": "job-1", "status": "running"}
            if url.endswith("/jobs")
            else {"job_id": "job-1", "status": "cancelling"}
        )

    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(filesystem.requests, "post", post)
    monkeypatch.setattr(
        filesystem.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            filesystem.requests.ConnectionError("lost")
        ),
    )
    monkeypatch.setattr(filesystem.time, "sleep", lambda _seconds: None)

    result = filesystem._run_in_isolated_runner("pytest -q", workspace)

    assert result == {"error": "Isolated runner is unavailable."}
    assert posts[-1].endswith("/jobs/job-1/cancel")
