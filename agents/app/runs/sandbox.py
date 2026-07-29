"""Disposable Git worktrees used for write-enabled agent runs."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.config import COMMAND_TIMEOUT_SECONDS, SANDBOX_ROOT
from ..core.processes import run_cancellable
from ..tools.filesystem import validate_workspace


@dataclass(frozen=True)
class Sandbox:
    repository: Path
    path: Path
    base_commit: str


def _git(
    directory: Path, *args: str, safe_directory: str | Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = ["git"]
    if safe_directory is not None:
        command.extend(["-c", f"safe.directory={safe_directory}"])
    command.extend(["-C", str(directory), *args])
    return run_cancellable(
        command,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )


def create_sandbox(workspace: str, run_id: str) -> Sandbox:
    requested = validate_workspace(workspace)
    # Host bind mounts commonly belong to a different UID than the container
    # process.  Permit Git ownership discovery only for this already-validated
    # workspace; no global Git configuration is changed.
    root = _git(requested, "rev-parse", "--show-toplevel", safe_directory="*")
    if root.returncode != 0:
        raise ValueError(
            "Write-enabled runs require a Git repository workspace: "
            f"{root.stderr.strip() or requested}"
        )
    repository = Path(root.stdout.strip()).resolve()
    head = _git(repository, "rev-parse", "HEAD", safe_directory=repository)
    if head.returncode != 0:
        raise RuntimeError("Could not determine repository base commit")
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    path = (SANDBOX_ROOT / run_id).resolve()
    if SANDBOX_ROOT not in path.parents:
        raise ValueError("Invalid sandbox path")
    created = _git(
        repository,
        "worktree",
        "add",
        "--detach",
        str(path),
        "HEAD",
        safe_directory=repository,
    )
    if created.returncode != 0:
        raise RuntimeError(f"Could not create Git worktree: {created.stderr.strip()}")
    return Sandbox(repository=repository, path=path, base_commit=head.stdout.strip())


def sandbox_diff(path: str) -> str:
    directory = Path(path).resolve()
    if SANDBOX_ROOT not in directory.parents:
        raise PermissionError("Sandbox path is outside the sandbox root")

    # `git diff` deliberately ignores untracked files.  Marking them as
    # intent-to-add makes them appear in the review diff without staging file
    # contents or changing the real repository's index (each worktree owns an
    # index).  Without this, write_file could report success and its new file
    # would be silently discarded when the sandbox was cleaned up.
    intent = _git(
        directory,
        "add",
        "--intent-to-add",
        "--force",
        "--all",
        safe_directory=directory,
    )
    if intent.returncode != 0:
        raise RuntimeError(intent.stderr.strip() or "Could not prepare sandbox diff")
    result = _git(
        directory, "diff", "--no-ext-diff", "--binary", safe_directory=directory
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not produce diff")
    return result.stdout


def merge_sandbox(sandbox: Sandbox) -> None:
    """Apply a reviewed sandbox diff onto the real repository.

    Uses `git apply` against the original repository rather than merging the
    worktree branch, since the sandbox is a detached, disposable worktree
    with no branch of its own -- the diff is the reviewable, approvable
    artifact.
    """
    diff = sandbox_diff(str(sandbox.path))
    if not diff.strip():
        return

    current = _git(
        sandbox.repository, "rev-parse", "HEAD", safe_directory=sandbox.repository
    )
    if current.returncode != 0 or current.stdout.strip() != sandbox.base_commit:
        raise RuntimeError(
            "Repository HEAD changed since this run started; refresh the run and resolve/retry "
            "instead of applying a stale diff."
        )

    # HEAD alone is not enough: a user may have local edits that an otherwise
    # cleanly-applying agent patch could overwrite. Approval is deliberately
    # conservative; commit/stash user changes or rerun from the new state.
    dirty = _git(
        sandbox.repository, "status", "--porcelain", safe_directory=sandbox.repository
    )
    if dirty.returncode != 0:
        raise RuntimeError(
            "Could not inspect repository status before applying sandbox diff"
        )
    if dirty.stdout.strip():
        raise RuntimeError(
            "Repository has uncommitted changes; commit or stash them before approving this run."
        )

    check = run_cancellable(
        [
            "git",
            "-C",
            str(sandbox.repository),
            "apply",
            "--check",
            "--whitespace=nowarn",
            "-",
        ],
        input=diff,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if check.returncode != 0:
        raise RuntimeError(
            f"Sandbox diff conflicts with the repository: {check.stderr.strip()}"
        )

    result = run_cancellable(
        ["git", "-C", str(sandbox.repository), "apply", "--whitespace=nowarn", "-"],
        input=diff,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not apply sandbox diff: {result.stderr.strip()}")


def remove_sandbox(repository: str, path: str) -> None:
    repo = Path(repository).resolve()
    directory = Path(path).resolve()
    if SANDBOX_ROOT not in directory.parents:
        raise PermissionError("Sandbox path is outside the sandbox root")
    result = _git(
        repo, "worktree", "remove", "--force", str(directory), safe_directory=repo
    )
    if result.returncode != 0 and directory.exists():
        raise RuntimeError(result.stderr.strip() or "Could not remove sandbox")
    if directory.exists():
        shutil.rmtree(directory)
