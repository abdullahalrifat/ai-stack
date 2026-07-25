import subprocess
from pathlib import Path

import pytest

from app.runs import sandbox


def make_repository(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True)
    (path / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True)


def test_git_can_use_an_explicit_safe_directory(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(sandbox.subprocess, "run", run)

    sandbox._git(tmp_path, "status", safe_directory=tmp_path)

    assert calls == [["git", "-c", f"safe.directory={tmp_path}", "-C", str(tmp_path), "status"]]


def test_sandbox_diff_includes_untracked_files(tmp_path, monkeypatch):
    """New files must be reviewable and survive an approved sandbox run."""
    sandbox_root = tmp_path / "sandboxes"
    worktree = sandbox_root / "run-1"
    worktree.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "SANDBOX_ROOT", sandbox_root)

    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True)
    (worktree / "created.py").write_text("print('created')\n", encoding="utf-8")

    diff = sandbox.sandbox_diff(str(worktree))

    assert "created.py" in diff
    assert "+print('created')" in diff


def test_merge_sandbox_applies_tracked_and_new_files(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    make_repository(repository)
    sandbox_root = tmp_path / "sandboxes"
    monkeypatch.setattr(sandbox, "SANDBOX_ROOT", sandbox_root)
    monkeypatch.setattr(sandbox, "validate_workspace", lambda path: Path(path).resolve())

    worktree = sandbox.create_sandbox(str(repository), "run-1")
    (worktree.path / "tracked.txt").write_text("after\n", encoding="utf-8")
    (worktree.path / "new.txt").write_text("new file\n", encoding="utf-8")

    sandbox.merge_sandbox(worktree)
    sandbox.remove_sandbox(str(worktree.repository), str(worktree.path))

    assert (repository / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert (repository / "new.txt").read_text(encoding="utf-8") == "new file\n"
    assert not worktree.path.exists()


def test_merge_sandbox_rejects_a_changed_base_commit(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    make_repository(repository)
    monkeypatch.setattr(sandbox, "SANDBOX_ROOT", tmp_path / "sandboxes")
    monkeypatch.setattr(sandbox, "validate_workspace", lambda path: Path(path).resolve())
    worktree = sandbox.create_sandbox(str(repository), "run-1")
    (worktree.path / "tracked.txt").write_text("agent edit\n", encoding="utf-8")

    (repository / "other.txt").write_text("concurrent commit\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "other.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "concurrent"], check=True, capture_output=True)

    with pytest.raises(RuntimeError, match="HEAD changed"):
        sandbox.merge_sandbox(worktree)
    sandbox.remove_sandbox(str(worktree.repository), str(worktree.path))
