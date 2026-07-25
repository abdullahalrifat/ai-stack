"""Disposable Git worktrees used for write-enabled agent runs."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import COMMAND_TIMEOUT_SECONDS, SANDBOX_ROOT


@dataclass(frozen=True)
class Sandbox:
    repository: Path
    path: Path


def _git(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(directory), *args],
        text=True,
        capture_output=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


def create_sandbox(workspace: str, run_id: str) -> Sandbox:
    requested = Path(workspace).resolve()
    root = _git(requested, "rev-parse", "--show-toplevel")
    if root.returncode != 0:
        raise ValueError("Write-enabled runs require a Git repository workspace.")
    repository = Path(root.stdout.strip()).resolve()
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    path = (SANDBOX_ROOT / run_id).resolve()
    if SANDBOX_ROOT not in path.parents:
        raise ValueError("Invalid sandbox path")
    created = _git(repository, "worktree", "add", "--detach", str(path), "HEAD")
    if created.returncode != 0:
        raise RuntimeError(f"Could not create Git worktree: {created.stderr.strip()}")
    return Sandbox(repository=repository, path=path)


def sandbox_diff(path: str) -> str:
    directory = Path(path).resolve()
    if SANDBOX_ROOT not in directory.parents:
        raise PermissionError("Sandbox path is outside the sandbox root")
    result = _git(directory, "diff", "--no-ext-diff", "--binary")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not produce diff")
    return result.stdout


def remove_sandbox(repository: str, path: str) -> None:
    repo = Path(repository).resolve()
    directory = Path(path).resolve()
    if SANDBOX_ROOT not in directory.parents:
        raise PermissionError("Sandbox path is outside the sandbox root")
    result = _git(repo, "worktree", "remove", "--force", str(directory))
    if result.returncode != 0 and directory.exists():
        raise RuntimeError(result.stderr.strip() or "Could not remove sandbox")
    if directory.exists():
        shutil.rmtree(directory)
