from pathlib import Path

import pytest

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


def test_write_edit_and_read_stay_inside_workspace(workspace):
    with filesystem.workspace_context(str(workspace)):
        assert filesystem.write_file.invoke(
            {"file_path": "src/example.txt", "content": "before"}
        )["status"] == "written"
        assert filesystem.edit_file.invoke(
            {
                "file_path": "src/example.txt",
                "old_string": "before",
                "new_string": "after",
            }
        )["status"] == "edited"
        assert filesystem.read_file.invoke({"file_path": "src/example.txt"}) == "after"
        assert "Access outside workspace denied" in filesystem.write_file.invoke(
            {"file_path": "../outside.txt", "content": "nope"}
        )["error"]


def test_run_command_enforces_policy_before_execution(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "ALLOWED_COMMANDS", ["echo"])
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(filesystem, "_run_in_isolated_runner", lambda command, cwd: {"command": command, "exit_code": 0, "output": "ok"})

    with filesystem.workspace_context(str(workspace)):
        assert "not permitted" in filesystem.run_command.invoke(
            {"command": "echo ok; echo unsafe"}
        )["error"]
        assert "not an approved command" in filesystem.run_command.invoke(
            {"command": "git status"}
        )["error"]
        result = filesystem.run_command.invoke({"command": "echo ok"})

    assert result["exit_code"] == 0
    assert result["output"] == "ok"
