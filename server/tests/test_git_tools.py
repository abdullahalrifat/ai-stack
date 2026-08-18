from pathlib import Path

import pytest

from app.core.permissions import PermissionPolicy, permissions_context
from app.tools import filesystem
from app.tools import git as git_tools


@pytest.fixture
def repo(tmp_path):
    import subprocess

    def run(*args, cwd=None):
        return subprocess.run(
            list(args),
            cwd=cwd or tmp_path,
            check=True,
            capture_output=True,
            text=True,
            env={"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path)},
        )

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("line1\n", encoding="utf-8")
    run("git", "add", "a.txt")
    run("git", "commit", "-qm", "init")
    return tmp_path


@pytest.fixture
def ctx(repo, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE_ROOTS", [repo])
    return repo


def test_git_status_reports_working_tree(ctx):
    (ctx / "a.txt").write_text("line1\nchanged\n", encoding="utf-8")
    (ctx / "new.txt").write_text("x\n", encoding="utf-8")

    with filesystem.workspace_context(str(ctx)):
        result = git_tools.git_status.invoke({"directory": "."})

    assert "M a.txt" in result["status"]
    assert "new.txt" in result["status"]


def test_git_log_lists_commits(ctx):
    with filesystem.workspace_context(str(ctx)):
        result = git_tools.git_log.invoke({"max_count": 5})

    assert "init" in result["log"]


def test_git_diff_shows_tracked_changes_and_untracked_names(ctx):
    (ctx / "a.txt").write_text("line1\nchanged\n", encoding="utf-8")
    (ctx / "new.txt").write_text("x\n", encoding="utf-8")

    with filesystem.workspace_context(str(ctx)):
        result = git_tools.git_diff.invoke({"directory": "."})

    assert "+changed" in result["diff"]
    assert "new.txt" in result["diff"]


def test_git_diff_rejects_unsafe_base(ctx):
    with filesystem.workspace_context(str(ctx)):
        result = git_tools.git_diff.invoke({"directory": ".", "base": "HEAD; rm -rf /"})

    assert result["error"]


def test_git_blame_attributes_lines(ctx):
    with filesystem.workspace_context(str(ctx)):
        result = git_tools.git_blame.invoke({"file_path": "a.txt"})

    assert "Test" in result["blame"]


def test_git_tools_error_on_non_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE_ROOTS", [tmp_path])
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")

    with filesystem.workspace_context(str(tmp_path)):
        result = git_tools.git_status.invoke({"directory": "."})

    assert result["error"]


def test_git_blame_rejects_missing_file(ctx):
    with filesystem.workspace_context(str(ctx)):
        result = git_tools.git_blame.invoke({"file_path": "missing.txt"})

    assert result["error"]
