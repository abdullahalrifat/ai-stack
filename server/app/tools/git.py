"""Read-only Git inspection tools.

The model frequently needs the repository's current state (what changed, what
is uncommitted, recent history, who touched a line) to plan edits and review
its own work.  `run_command` already exposes `git`, but it round-trips through
the disposable command runner; these tools run in-process and are strictly
read-only -- no index writes, no config changes, no network, and every
subcommand is hard-coded here so the model can never smuggle in flags or
mutating operations.
"""

import logging

from langchain.tools import tool

from app.core.config import COMMAND_TIMEOUT_SECONDS, MAX_TOOL_OUTPUT_CHARS
from app.core.processes import run_cancellable
from app.tools.filesystem import resolve_path

logger = logging.getLogger(__name__)


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:MAX_TOOL_OUTPUT_CHARS] + (
        f"\n... [output truncated at {MAX_TOOL_OUTPUT_CHARS} characters]"
    )


def _git(directory, *args: str) -> str:
    result = run_cancellable(
        ["git", "-C", str(directory), *args],
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "git failed").strip()
        )
    return result.stdout


def _workspace_for(directory: str):
    return resolve_path(directory)


@tool
def git_status(directory: str = "."):
    """Show the repository's working-tree status (read-only).

    Reports the current branch, tracked modifications, staged changes, and
    untracked files. Use before planning edits or after running tests to see
    what changed.
    """
    try:
        workspace = _workspace_for(directory)
        return {"status": _truncate(_git(workspace, "status", "--short", "--branch"))}
    except Exception as e:
        logger.exception("git_status failed")
        return {"error": str(e)}


@tool
def git_diff(directory: str = ".", base: str = "HEAD", max_chars: int = 0):
    """Show a unified diff of uncommitted changes (read-only).

    Reports tracked modifications against the given base (default HEAD) and
    lists untracked files by name (their contents are not in a diff). Use to
    review exactly what a change touched before finalizing.
    """
    try:
        if not base or any(c in base for c in "&|;<>`$ \t"):
            return {"error": "Invalid base ref."}
        workspace = _workspace_for(directory)
        diff = _git(workspace, "diff", "--no-ext-diff", base)
        untracked = _git(workspace, "ls-files", "--others", "--exclude-standard")
        parts = [diff]
        if untracked.strip():
            parts.append("\nUntracked files:\n" + untracked)
        text = "\n".join(parts)
        if max_chars and max_chars > 0:
            text = text[:max_chars]
        return {"diff": _truncate(text)}
    except Exception as e:
        logger.exception("git_diff failed")
        return {"error": str(e)}


@tool
def git_log(directory: str = ".", max_count: int = 10):
    """Show recent commit history one-line per commit (read-only)."""
    try:
        max_count = max(1, min(int(max_count), 100))
        workspace = _workspace_for(directory)
        return {"log": _truncate(_git(workspace, "log", "--oneline", "-n", str(max_count)))}
    except Exception as e:
        logger.exception("git_log failed")
        return {"error": str(e)}


@tool
def git_blame(file_path: str, directory: str = "."):
    """Show the commit and author responsible for each line of a file (read-only)."""
    try:
        path = resolve_path(file_path)
        if not path.exists():
            return {"error": "File not found"}
        if not path.is_file():
            return {"error": "Path is not a file"}
        workspace = _workspace_for(directory)
        return {"blame": _truncate(_git(workspace, "blame", "--", str(path)))}
    except Exception as e:
        logger.exception("git_blame failed")
        return {"error": str(e)}
