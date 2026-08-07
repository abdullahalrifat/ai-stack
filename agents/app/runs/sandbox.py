"""Disposable Git clones used for reviewable agent runs."""

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
    bundle_path = (SANDBOX_ROOT / f".{run_id}.bundle").resolve()
    if SANDBOX_ROOT not in bundle_path.parents:
        raise ValueError("Invalid sandbox bundle path")
    try:
        bundled = _git(
            repository,
            "bundle",
            "create",
            str(bundle_path),
            "HEAD",
            safe_directory="*",
        )
        if bundled.returncode != 0:
            raise RuntimeError(f"Could not prepare sandbox: {bundled.stderr.strip()}")
        created = _git(
            SANDBOX_ROOT,
            "clone",
            "--no-checkout",
            "--",
            str(bundle_path),
            str(path),
        )
    finally:
        bundle_path.unlink(missing_ok=True)
    if created.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        raise RuntimeError(f"Could not create sandbox clone: {created.stderr.strip()}")
    checkout = _git(path, "checkout", "--detach", head.stdout.strip())
    if checkout.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        raise RuntimeError(f"Could not initialize sandbox: {checkout.stderr.strip()}")
    return Sandbox(repository=repository, path=path, base_commit=head.stdout.strip())


def sandbox_diff(path: str) -> str:
    directory = Path(path).resolve()
    if SANDBOX_ROOT not in directory.parents:
        raise PermissionError("Sandbox path is outside the sandbox root")

    # `git diff` deliberately ignores untracked files.  Marking them as
    # intent-to-add makes them appear in the review diff without staging file
    # contents or changing the real repository's index (each worktree owns an
    # index).  Without this, write_file could report success and its new file
    # would be silently discarded when the sandbox was cleaned up.  Ignored
    # files (build caches such as `.ruff_cache/`) stay out of the diff so they
    # are not reviewable or approvable.
    intent = _git(
        directory,
        "add",
        "--intent-to-add",
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

    # The repository is not required to be clean or at the recorded base
    # commit. `git apply --check` verifies the patch still applies to the
    # current working tree; only then is it applied. Local edits outside the
    # patch's hunks are preserved, and genuine conflicts fail loudly -- just
    # as they would for a direct edit.
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
    # Current sandboxes are independent clones, so cleanup never needs to
    # mutate the source repository's protected .git directory. Retain support
    # for older pending runs that were created as linked worktrees.
    if (directory / ".git").is_file():
        result = _git(
            repo, "worktree", "remove", "--force", str(directory), safe_directory=repo
        )
        if result.returncode != 0 and directory.exists():
            raise RuntimeError(result.stderr.strip() or "Could not remove sandbox")
    if directory.exists():
        shutil.rmtree(directory)
