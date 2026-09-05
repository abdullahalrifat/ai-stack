import asyncio
import difflib
import importlib.util
import logging
import os
import re
import shlex
import shutil
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import requests
from app.core.cancellation import cancellation_requested
from app.core.config import (
    ALLOWED_COMMANDS,
    COMMAND_TIMEOUT_SECONDS,
    MAX_TOOL_OUTPUT_CHARS,
    RUN_COMMANDS_ALLOW_NETWORK,
    RUNNER_API_KEY,
    RUNNER_URL,
    SANDBOX_ROOT,
    WORKSPACE_ROOTS,
)
from app.core.config import (
    DEFAULT_WORKSPACE as CONFIGURED_DEFAULT_WORKSPACE,
)
from app.core.exceptions import RunCancelled
from app.core.permissions import active_policy
from langchain.tools import tool
from pathspec import PathSpec

# ============================================================
# Configuration
# ============================================================

DEFAULT_WORKSPACE = CONFIGURED_DEFAULT_WORKSPACE


# Context-local workspace prevents one HTTP request from changing another
# request's filesystem boundary.
CURRENT_WORKSPACE: ContextVar[Path] = ContextVar(
    "current_workspace", default=DEFAULT_WORKSPACE
)
SOURCE_WORKSPACE: ContextVar[Path | None] = ContextVar("source_workspace", default=None)


IGNORE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "venv",
    ".venv",
    "dist",
    "build",
    # Docker volume/state directories are not source code. They are often
    # unreadable from the agent container and can contain huge model/database
    # artifacts that waste an entire repository-review run.
    "agent-sandboxes",
    "ollama",
    "postgres",
    "qdrant",
    "redis",
    "cache",
}

# These paths are not useful source context and can contain multi-gigabyte
# artifacts. Unlike repository ignore patterns, mandatory exclusions cannot be
# negated by .aistackignore and cannot be read explicitly.
# Avoid a blanket `models/` hard block: Django and other applications commonly
# keep real source there. Repositories may scan-exclude it in .aistackignore,
# while actual weight suffixes remain blocked everywhere.
MODEL_DIRS = {"checkpoints", "model_weights", "weights"}
MODEL_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
}
ARCHIVE_SUFFIXES = {".7z", ".bz2", ".gz", ".rar", ".tar", ".tgz", ".xz", ".zip"}
DATABASE_SUFFIXES = {".db", ".dump", ".sqlite", ".sqlite3"}

SAFE_ENV_FILES = {".env.example", ".env.sample", ".env.template"}
SENSITIVE_FILE_NAMES = {
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}


MAX_FILE_SIZE = 100_000
MAX_EDIT_FILE_SIZE = 2_000_000
MAX_SCAN_FILES = 5_000
MAX_SEARCH_FILE_SIZE = 512_000
MAX_INSPECT_PATHS = 20
MAX_DIRECTORY_ENTRIES = 1_000
MAX_CODE_SEARCH_MATCHES = 100
MAX_CODE_SEARCH_CONTEXT_LINES = 5
RUNNER_POLL_SECONDS = 0.20
RUNNER_REQUEST_TIMEOUT_SECONDS = 5


def _run_in_isolated_runner(command: str, cwd: Path, tier: str = "isolated") -> dict:
    if SANDBOX_ROOT not in cwd.parents:
        return {"error": "Commands may run only inside a disposable sandbox worktree."}
    job_id: str | None = None

    def cancel_job() -> None:
        if job_id is None:
            return
        try:
            requests.post(
                f"{RUNNER_URL}/jobs/{job_id}/cancel",
                headers={"X-Runner-Key": RUNNER_API_KEY or ""},
                timeout=RUNNER_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            pass

    try:
        response = requests.post(
            f"{RUNNER_URL}/jobs",
            json={
                "command": command,
                "directory": str(cwd),
                "tier": tier,
            },
            headers={"X-Runner-Key": RUNNER_API_KEY or ""},
            timeout=RUNNER_REQUEST_TIMEOUT_SECONDS,
        )
        if not response.ok:
            return {"error": "Isolated runner rejected command."}
        payload = response.json()
        job_id = str(payload["job_id"])
        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS + 15
        while payload.get("status") not in {
            "completed",
            "cancelled",
            "timed_out",
            "kill_failed",
        }:
            if cancellation_requested():
                try:
                    cancel_job()
                finally:
                    raise RunCancelled()
            if time.monotonic() >= deadline:
                cancel_job()
                return {"error": "Isolated runner status timed out.", "job_id": job_id}
            time.sleep(RUNNER_POLL_SECONDS)
            response = requests.get(
                f"{RUNNER_URL}/jobs/{job_id}",
                headers={"X-Runner-Key": RUNNER_API_KEY or ""},
                timeout=RUNNER_REQUEST_TIMEOUT_SECONDS,
            )
            if not response.ok:
                cancel_job()
                return {
                    "error": "Could not read isolated runner job.",
                    "job_id": job_id,
                }
            payload = response.json()
        if payload["status"] == "cancelled":
            raise RunCancelled()
        if payload["status"] == "kill_failed":
            return {
                "error": "Isolated runner could not terminate the command.",
                "job_id": job_id,
                "status": "kill_failed",
            }
        return {
            "command": command,
            "job_id": job_id,
            "status": payload["status"],
            "exit_code": payload["exit_code"],
            "output": payload.get("output", ""),
        }
    except requests.RequestException:
        cancel_job()
        return {"error": "Isolated runner is unavailable."}


# Module logger
logger = logging.getLogger(__name__)


# ============================================================
# Helpers
# ============================================================


def validate_workspace(path: str, *, allow_sandbox: bool = False) -> Path:
    """Resolve and validate a requested workspace path.

    The path must exist, be a directory, and fall inside one of the
    configured WORKSPACE_ROOTS (see app.config) -- supporting multiple
    mounted repositories/directories rather than a single hardcoded root.
    """

    new_path = Path(path).resolve()

    if not new_path.exists():
        raise ValueError(f"Workspace does not exist: {new_path}")

    if not new_path.is_dir():
        raise ValueError(f"Workspace is not a directory: {new_path}")

    allowed_workspace = any(
        new_path == root or root in new_path.parents for root in WORKSPACE_ROOTS
    )
    allowed_sandbox = (
        allow_sandbox
        and new_path.parent == SANDBOX_ROOT
        and (new_path / ".git").is_dir()
    )

    if not (allowed_workspace or allowed_sandbox):
        allowed_list = ", ".join(str(r) for r in WORKSPACE_ROOTS)
        raise PermissionError(
            f"Workspace must be inside one of the configured roots: {allowed_list}"
        )

    return new_path


def resolve_request_workspace(path: str | None, message: str = "") -> str:
    """Choose a narrow workspace named in a request without trusting it blindly.

    API clients such as Continue and Open WebUI normally cannot send this
    stack's optional ``workspace`` field. When they explicitly mention a
    mounted repository path in their request, use that repository instead of
    scanning the configured broad default. An explicit non-default workspace
    field always wins.
    """

    requested = validate_workspace(path or str(DEFAULT_WORKSPACE))
    if requested != DEFAULT_WORKSPACE:
        return str(requested)

    candidates = re.findall(r"(?<![\w/])(/[A-Za-z0-9._/-]+)", message)
    for candidate in sorted(set(candidates), key=len, reverse=True):
        try:
            resolved = validate_workspace(candidate.rstrip(".,:;!?)]}\"'"))
        except (ValueError, PermissionError):
            continue
        if resolved != DEFAULT_WORKSPACE:
            return str(resolved)
    return str(requested)


def workspace_choices() -> list[str]:
    """List allowed roots and their immediate Git repositories for UI selection."""

    choices: set[Path] = {DEFAULT_WORKSPACE}
    for root in WORKSPACE_ROOTS:
        choices.add(root)
        try:
            for index, child in enumerate(root.iterdir()):
                if index >= MAX_SCAN_FILES:
                    break
                if child.is_dir() and (child / ".git").exists():
                    choices.add(child.resolve())
        except OSError:
            continue
    return [str(path) for path in sorted(choices)]


@contextmanager
def workspace_context(
    path: str,
    *,
    allow_sandbox: bool = False,
    source_workspace: str | None = None,
):
    token = CURRENT_WORKSPACE.set(validate_workspace(path, allow_sandbox=allow_sandbox))
    source_token = SOURCE_WORKSPACE.set(
        Path(source_workspace).resolve() if source_workspace else None
    )
    try:
        yield
    finally:
        SOURCE_WORKSPACE.reset(source_token)
        CURRENT_WORKSPACE.reset(token)


def current_workspace() -> Path:
    return CURRENT_WORKSPACE.get()


def resolve_path(path: str, *, unique_basename: bool = True) -> Path:
    """
    Resolve a path relative to the current workspace.

    Supports both:
        docker-compose.yml
        server/app/main.py
        /workspace/docker-compose.yml
    """

    p = Path(path)

    # In reviewable runs the model works in a disposable clone but may repeat
    # an absolute path it discovered before sandbox creation. Map that
    # request-scoped source path to the same relative location in the clone;
    # never broaden the sandbox's filesystem boundary.
    source_workspace = SOURCE_WORKSPACE.get()
    if p.is_absolute() and source_workspace is not None:
        try:
            source_relative = p.relative_to(source_workspace)
        except ValueError:
            pass
        else:
            p = current_workspace() / source_relative

    # Models occasionally singularize the displayed ``/sandboxes/<run>``
    # root as ``/sandbox/<run>``. Accept only an alias naming this exact
    # active run; a different run ID or any other absolute path remains
    # outside the workspace boundary.
    workspace = current_workspace()
    if (
        p.is_absolute()
        and workspace.parent == SANDBOX_ROOT
        and len(p.parts) >= 3
        and p.parts[1] in {"sandbox", "sandboxes", "agent-sandboxes"}
    ):
        if p.parts[2] == workspace.name:
            p = workspace.joinpath(*p.parts[3:])
        else:
            logger.debug(
                "resolve_path: rejected sandbox alias for different run. requested=%s resolved=%s workspace=%s",
                path,
                p,
                workspace,
            )
            raise PermissionError("Access outside workspace denied.")

    # Small local models occasionally omit the leading slash when repeating an
    # in-container absolute path (for example ``workspace/ai-stack``). Treat
    # that form as absolute only when its first component identifies an
    # allowed workspace root; all other relative paths remain relative to the
    # active repository.
    if not p.is_absolute() and p.parts:
        root_names = {root.parts[1] for root in WORKSPACE_ROOTS if len(root.parts) > 1}
        if p.parts[0] in root_names:
            p = Path("/") / p

    if not p.is_absolute():
        p = current_workspace() / p

    # If the requested path doesn't exist as written, try a safe workspace-wide
    # lookup for a unique match with the same basename. This helps small models
    # that emit a short path like `src` when the repository's nested layout is
    # `jarvis/src` or similar. Only accept a single unambiguous candidate. Limit
    # the search breadth to avoid long-running file system scans.
    #
    # Writes disable this: a write to a path that does not exist is a request to
    # CREATE that file, and redirecting it to a different existing file with the
    # same basename would silently clobber unrelated code (a common 8B-model
    # failure that destroyed server/app/agent/executor.py in production).
    if unique_basename and not p.exists():
        name = p.name
        candidates = []
        try:
            for i, candidate in enumerate(current_workspace().rglob(name)):
                if i >= 200:
                    break
                if ignored(candidate):
                    continue
                candidates.append(candidate)
        except OSError as exc:
            logger.debug("resolve_path: error while globbing workspace: %s", exc)
            candidates = []
        if len(candidates) == 1:
            candidate = candidates[0]
            # Only accept candidate if it's a sensible substitute under the
            # current workspace and the original request had a short path.
            if (
                current_workspace() in candidate.parents
                or candidate == current_workspace()
            ):
                logger.debug(
                    "resolve_path: mapped short path '%s' to '%s' inside workspace",
                    path,
                    candidate,
                )
                p = candidate
            else:
                logger.debug(
                    "resolve_path: candidate '%s' outside current workspace, rejecting",
                    candidate,
                )

    try:
        p = p.resolve()
    except Exception as exc:
        logger.debug("resolve_path: could not resolve path %s: %s", p, exc)
        raise

    workspace = current_workspace()
    if p != workspace and workspace not in p.parents:
        logger.debug(
            "resolve_path: denied access outside workspace. requested=%s resolved=%s workspace=%s source_workspace=%s",
            path,
            p,
            workspace,
            SOURCE_WORKSPACE.get(),
        )
        raise PermissionError("Access outside workspace denied.")

    return p


def read_exclusion_reason(path: Path) -> str:
    """Return a non-overridable reason a path cannot enter model context."""

    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in MODEL_SUFFIXES or any(
        part.casefold() in MODEL_DIRS for part in path.parts
    ):
        return "model artifact"
    if suffix in ARCHIVE_SUFFIXES:
        return "archive"
    if suffix in DATABASE_SUFFIXES:
        return "database artifact"
    if (
        name == ".env"
        or (name.startswith(".env.") and name not in SAFE_ENV_FILES)
        or name in SENSITIVE_FILE_NAMES
        or suffix in {".key", ".pem", ".p12", ".pfx"}
    ):
        return "credential or secret"
    return ""


def sensitive(path: Path) -> bool:
    return bool(read_exclusion_reason(path))


def _ignore_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    signature = []
    for name in (".gitignore", ".aistackignore"):
        path = root / name
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


@lru_cache(maxsize=64)
def _repository_ignore_spec(
    root_text: str, signature: tuple[tuple[str, int, int], ...]
) -> PathSpec:
    """Compile root ignore files, refreshing automatically when they change."""

    root = Path(root_text)
    patterns = []
    for name, _, _ in signature:
        try:
            patterns.extend((root / name).read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError):
            continue
    return PathSpec.from_lines("gitignore", patterns)


def repository_ignored(path: Path) -> bool:
    """Return whether root .gitignore/.aistackignore excludes a path from scans."""

    root = current_workspace()
    try:
        relative_path = path.relative_to(root).as_posix()
    except ValueError:
        return False
    if path.is_dir() and relative_path:
        relative_path += "/"
    signature = _ignore_signature(root)
    return bool(
        signature
        and _repository_ignore_spec(str(root), signature).match_file(relative_path)
    )


def ignored(path: Path) -> bool:
    if (
        any(
            part in IGNORE_DIRS
            or part.casefold() in MODEL_DIRS
            or part.endswith(".egg-info")
            for part in path.parts
        )
        or sensitive(path)
        or repository_ignored(path)
    ):
        return True
    # The Compose-managed Open WebUI directory is persistent runtime data,
    # not this repository's frontend source (that lives in runs-ui). Detect
    # its upload-only shape instead of globally ignoring every checkout named
    # "open-webui".
    for candidate in (path, *path.parents):
        if candidate == current_workspace():
            break
        if (
            candidate.name == "open-webui"
            and (candidate / "uploads").is_dir()
            and not (candidate / "package.json").is_file()
        ):
            return True
    return False


def relative(path: Path):
    return str(path.relative_to(current_workspace()))


def _read_utf8_text(path: Path) -> str:
    """Read source text without leaking binary control bytes into events."""

    raw = path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("File is binary, not UTF-8 text.")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("File is not valid UTF-8 text.") from exc


# ============================================================
# Workspace
# ============================================================


@tool
def workspace_root():
    """
    Return the active workspace location.
    """

    return {"workspace": str(current_workspace())}


# ============================================================
# Tree
# ============================================================


@tool
def tree(
    directory: str = ".",
    depth: int = 2,
):
    """
    Show project tree.
    """

    try:
        root = resolve_path(directory)

        output = []

        def walk(path: Path, level: int):
            if level > depth:
                return

            try:
                entries = sorted(
                    path.iterdir(),
                    key=lambda x: (
                        x.is_file(),
                        x.name.lower(),
                    ),
                )
            except PermissionError:
                output.append("  " * level + "[unreadable]")
                return

            for entry in entries:
                if len(output) >= MAX_DIRECTORY_ENTRIES:
                    output.append("  " * level + "[truncated]")
                    return
                if ignored(entry):
                    continue

                output.append("  " * level + entry.name)

                if entry.is_dir():
                    walk(entry, level + 1)

        walk(root, 0)

        return "\n".join(output)

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# List Files
# ============================================================


@tool
def list_files(
    directory: str = ".",
):
    """
    List files in a directory.
    """

    try:
        # Small models sometimes pass a filename or a glob despite this
        # tool's directory-oriented schema. Return useful bounded matches so
        # one malformed inspection call does not derail an editing run.
        if any(character in directory for character in "*?["):
            pattern = directory
            for root in filter(None, (current_workspace(), SOURCE_WORKSPACE.get())):
                root_text = str(root)
                if pattern == root_text:
                    pattern = "."
                    break
                if pattern.startswith(root_text + os.sep):
                    pattern = pattern[len(root_text) + 1 :]
                    break
            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                return {"error": "Glob must stay inside the active workspace."}
            # pathlib reserves a full ``**`` component for recursion and
            # rejects doubled stars embedded in a filename. Treat those as a
            # normal wildcard, which is what the caller intended.
            pattern = "/".join(
                part if part == "**" else part.replace("**", "*")
                for part in Path(pattern).parts
            )
            matches = []
            for candidate in sorted(current_workspace().glob(pattern)):
                candidate = candidate.resolve()
                if ignored(candidate):
                    continue
                if (
                    candidate != current_workspace()
                    and current_workspace() not in candidate.parents
                ):
                    continue
                matches.append(
                    {
                        "name": candidate.name,
                        "path": relative(candidate),
                        "type": "directory" if candidate.is_dir() else "file",
                    }
                )
                if len(matches) >= MAX_DIRECTORY_ENTRIES:
                    break
            return matches

        path = resolve_path(directory)

        if path.is_file():
            if ignored(path):
                return []
            return [{"name": path.name, "path": relative(path), "type": "file"}]

        if not path.exists():
            logger.debug(
                "list_files: Directory not found: %s (resolved: %s)", directory, path
            )
            return {
                "error": f"Directory not found: {directory}",
                "attempted_path": str(path),
            }

        files = []

        for index, file in enumerate(sorted(path.iterdir())):
            if index >= MAX_DIRECTORY_ENTRIES:
                break
            if ignored(file):
                continue

            files.append(
                {
                    "name": file.name,
                    "path": relative(file),
                    "type": "directory" if file.is_dir() else "file",
                }
            )

        return files

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Read File
# ============================================================


@tool
def read_file(
    file_path: str,
    start_line: int = 1,
    end_line: int = 0,
):
    """
    Read a UTF-8 text file.
    """

    try:
        path = resolve_path(file_path)

        if not path.exists():
            logger.debug(
                "read_file: File not found: %s (resolved: %s)", file_path, path
            )
            return {
                "error": "File not found",
                "attempted_path": str(path),
                "workspace": str(current_workspace()),
            }

        exclusion_reason = read_exclusion_reason(path)
        if exclusion_reason:
            logger.debug("read_file: Attempt to read sensitive file: %s", path)
            return {
                "error": "Reading excluded files is not allowed.",
                "excluded": True,
                "reason": exclusion_reason,
            }

        file_size = path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            # Do not turn a known large source path into a dead end. Stream an
            # explicit line range, or return a bounded first slice with a
            # machine-readable continuation hint instead of loading the file.
            start = max(1, int(start_line))
            end = max(start, int(end_line)) if end_line else None
            selected = []
            selected_chars = 0
            truncated = False
            preview_limit = min(MAX_FILE_SIZE, MAX_TOOL_OUTPUT_CHARS)
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if line_number < start:
                        continue
                    if end is not None and line_number > end:
                        break
                    if "\x00" in line:
                        return {"error": "Binary files are not supported."}
                    if selected_chars + len(line) > preview_limit:
                        truncated = True
                        break
                    selected.append(line)
                    selected_chars += len(line)
            text = "".join(selected)
            if truncated or end is None:
                next_line = start + len(selected)
                text += (
                    f"\n...[large file preview: {file_size} bytes; "
                    f"continue with start_line={next_line} and a bounded end_line]"
                )
            return text

        text = _read_utf8_text(path)
        if start_line != 1 or end_line:
            lines = text.splitlines(keepends=True)
            start = max(1, int(start_line))
            end = (
                len(lines)
                if not end_line
                else min(len(lines), max(start, int(end_line)))
            )
            return "".join(lines[start - 1 : end])
        return text

    except Exception as e:
        logger.exception("read_file: unexpected error reading %s", file_path)
        return {"error": str(e)}


# ============================================================
# Find File
# ============================================================


@tool
def find_file(
    filename: str,
):
    """
    Find files by filename.
    """

    try:
        matches = []

        for index, file in enumerate(walk_files(current_workspace())):
            if index >= MAX_SCAN_FILES:
                break

            if filename.lower() in file.name.lower():
                matches.append(relative(file))
                if len(matches) >= 100:
                    break

        return matches[:100]

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Search Text
# ============================================================


def walk_files(root: Path):
    """Yield files deterministically while pruning ignored subtrees.

    The model often passes the file it is investigating as ``directory``
    (``server/app/runner.py``) instead of its parent folder. Treat a file
    path as a single-file search rather than silently returning no matches,
    which previously made weak edit-calling models conclude the symbol does
    not exist anywhere. Unlike ``Path.rglob``, this does not descend through
    dependency, cache, database, or model-storage directories before ignoring
    their results.
    """
    if root.is_file():
        # A direct file argument is an explicit, narrow request. Permit safe
        # repository-ignored files while retaining mandatory read exclusions.
        if not sensitive(root):
            yield root
        return
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirnames[:] = sorted(name for name in dirnames if not ignored(parent / name))
        for name in sorted(filenames):
            path = parent / name
            if not ignored(path):
                yield path


@tool
def search_text(
    keyword: str,
    directory: str = ".",
):
    """
    Search text inside files.
    """

    try:
        root = resolve_path(directory)

        matches = []

        for index, file in enumerate(walk_files(root)):
            if index >= MAX_SCAN_FILES:
                break
            if root.is_file() is False and ignored(file):
                continue
            if file.stat().st_size > MAX_SEARCH_FILE_SIZE:
                continue

            try:
                text = file.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )

            except Exception:
                continue

            if keyword.lower() in text.lower():
                matches.append(relative(file))
                if len(matches) >= 100:
                    break

        return matches[:100]

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Search Code
# ============================================================


@tool
def search_code(
    pattern: str,
    directory: str = ".",
    file_glob: str = "",
    context_lines: int = 2,
    ignore_case: bool = False,
):
    """Search file contents for a regular expression with line numbers and context.

    Returns each matching line with its 1-based line number, the matched text,
    and a bounded window of surrounding context, sorted by file path and line
    number. The pattern is treated as a regular expression; patterns without
    metacharacters are matched as plain case-sensitive substrings unless
    ignore_case is set. file_glob restricts the search to matching file paths
    (for example "*.py" or "tests/*.py"). Results are bounded to the first 100
    matches across at most 5000 files.
    """

    try:
        root = resolve_path(directory)
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return {"error": f"Invalid regular expression: {exc}"}

        context_lines = max(0, min(int(context_lines), MAX_CODE_SEARCH_CONTEXT_LINES))
        matches = []

        for index, file in enumerate(walk_files(root)):
            if index >= MAX_SCAN_FILES:
                break
            if root.is_file() is False and (ignored(file) or not file.is_file()):
                continue
            if file.stat().st_size > MAX_SEARCH_FILE_SIZE:
                continue
            if file_glob and not file.match(file_glob):
                continue

            try:
                text = _read_utf8_text(file)
            except ValueError:
                continue
            lines = text.splitlines()

            for line_index, line in enumerate(lines, start=1):
                if not regex.search(line):
                    continue
                matches.append(
                    {
                        "path": relative(file),
                        "line": line_index,
                        "text": line,
                        "context": {
                            "before": lines[
                                max(0, line_index - 1 - context_lines) : line_index - 1
                            ],
                            "after": lines[line_index : line_index + context_lines],
                        },
                    }
                )
                if len(matches) >= MAX_CODE_SEARCH_MATCHES:
                    return {"matches": matches, "truncated": True}

        return {"matches": matches, "truncated": False}

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Project Summary
# ============================================================


@tool
def project_summary():
    """
    Summarize the mounted project.
    """

    extensions = {}
    important = []

    try:
        for index, file in enumerate(walk_files(current_workspace())):
            if index >= MAX_SCAN_FILES:
                break

            ext = file.suffix.lower()
            extensions[ext] = extensions.get(ext, 0) + 1

            if file.name in {
                "docker-compose.yml",
                "docker-compose.yaml",
                "Dockerfile",
                "README.md",
                "TODO.md",
                "ROADMAP.md",
                "requirements.txt",
                "pyproject.toml",
                "package.json",
                ".env.example",
            }:
                important.append(relative(file))
    except OSError:
        # A mounted volume may contain unreadable runtime state. The summary
        # remains useful from the source paths that were readable.
        pass

    return {
        "workspace": str(current_workspace()),
        "important_files": important,
        "languages": extensions,
    }


@tool
def inspect_test_environment(directory: str = "."):
    """Describe test configuration and stable runner capabilities."""

    try:
        root = resolve_path(directory)
        if not root.is_dir():
            return {"error": "directory is not a directory."}
        config_names = (
            "pytest.ini",
            "pyproject.toml",
            "setup.cfg",
            "tox.ini",
            "package.json",
        )
        configs = [name for name in config_names if (root / name).is_file()]
        test_directories = []
        if (root / "tests").is_dir():
            test_directories.append("tests")
        try:
            children = sorted(
                (child for child in root.iterdir() if child.is_dir()),
                key=lambda child: child.name.casefold(),
            )
        except OSError:
            children = []
        for child in children[:100]:
            if ignored(child):
                continue
            child_tests = child / "tests"
            if child_tests.is_dir():
                test_directories.append(relative(child_tests))
            for name in config_names:
                candidate = child / name
                if candidate.is_file():
                    candidate_name = relative(candidate)
                    if candidate_name not in configs:
                        configs.append(candidate_name)
        virtualenvs = []
        for name in ("venv", ".venv"):
            candidate = root / name / "bin"
            if candidate.is_dir():
                virtualenvs.append(
                    {
                        "path": relative(candidate.parent),
                        "pytest": (candidate / "pytest").is_file(),
                        "python": (candidate / "python").is_file(),
                    }
                )
        coverage_target = ""
        if (root / "app").is_dir():
            coverage_target = "app"
        elif (root / "src").is_dir():
            coverage_target = "src"
        coverage_runs = []
        for tests_path in test_directories:
            tests_directory = root / tests_path
            package_root = tests_directory.parent
            target = ""
            if (package_root / "app").is_dir():
                target = "app"
            elif (package_root / "src").is_dir():
                packages = sorted(
                    child.name
                    for child in (package_root / "src").iterdir()
                    if child.is_dir() and (child / "__init__.py").is_file()
                )
                target = packages[0] if packages else ""
            if target:
                coverage_runs.append(
                    {
                        "directory": relative(package_root),
                        "coverage_target": target,
                    }
                )
        return {
            "directory": relative(root),
            "configs": configs,
            "test_directories": test_directories,
            "virtualenvs": virtualenvs,
            "runner": {
                "pytest": bool(shutil.which("pytest")),
                "pytest_cov": importlib.util.find_spec("pytest_cov") is not None,
                "ruff": bool(shutil.which("ruff")),
            },
            "recommended": {
                "test_kind": "pytest",
                "coverage_kind": "pytest_coverage",
                "coverage_target": coverage_target,
            },
            "coverage_runs": coverage_runs,
            "fresh_shell_per_command": True,
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Inspect Multiple Files
# ============================================================


@tool
def inspect_files(
    paths: list[str],
):
    """
    Inspect multiple files or directories.

    Useful for repository analysis.
    """

    results = []

    try:
        requested_paths = paths[:MAX_INSPECT_PATHS]
        # Divide the model-facing budget across requested files so the first
        # large README cannot erase every later manifest or roadmap entry when
        # AgentState applies its final aggregate bound.
        content_budget = max(
            512,
            min(
                MAX_FILE_SIZE,
                (MAX_TOOL_OUTPUT_CHARS - 1_500) // max(1, len(requested_paths)),
            ),
        )
        for item in requested_paths:
            path = resolve_path(item)

            if not path.exists():
                results.append({"path": item, "error": "Not found"})
                continue

            exclusion_reason = read_exclusion_reason(path)
            if exclusion_reason:
                results.append(
                    {
                        "path": item,
                        "error": "Reading excluded files is not allowed.",
                        "excluded": True,
                        "reason": exclusion_reason,
                    }
                )
                continue

            if path.is_dir():
                files = []

                for index, f in enumerate(path.iterdir()):
                    if index >= 100:
                        break
                    if ignored(f):
                        continue

                    files.append(f.name)

                results.append(
                    {"path": item, "type": "directory", "contents": files[:100]}
                )

            else:
                if path.stat().st_size > MAX_FILE_SIZE:
                    results.append({"path": item, "error": "File too large"})
                    continue

                try:
                    content = _read_utf8_text(path)[:content_budget]
                except ValueError as exc:
                    results.append({"path": item, "error": str(exc)})
                    continue
                results.append({"path": item, "type": "file", "content": content})

        return {"items": results, "truncated": len(paths) > MAX_INSPECT_PATHS}

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Controlled coding tools
# ============================================================


@tool
def write_file(
    file_path: str, content: str, overwrite: bool = False, dry_run: bool = False
):
    """Create a UTF-8 text file in the active workspace.

    This tool is deliberately small: it cannot access paths outside the
    request-scoped workspace and refuses to replace an existing file unless
    overwrite is explicitly true. Prefer edit_file for modifying existing
    files.
    """
    try:
        path = resolve_path(file_path, unique_basename=False)
        active_policy().check_write(path, current_workspace())
        if path.exists() and not overwrite:
            return {
                "error": "File exists; reread it and set overwrite=true to replace it."
            }
        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            return {"error": "Content exceeds maximum size."}

        if dry_run:
            # Do not modify filesystem; return a preview of the intended action.
            logger.debug(
                "write_file dry_run: would write %s (%d bytes)",
                path,
                len(content.encode("utf-8")),
            )
            return {
                "status": "dry_run",
                "action": "write",
                "path": relative(path),
                "bytes": len(content.encode("utf-8")),
            }

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "status": "written",
            "path": relative(path),
            "bytes": path.stat().st_size,
        }
    except Exception as e:
        logger.exception("write_file: unexpected error writing %s", file_path)
        return {"error": str(e)}


@tool
def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    dry_run: bool = False,
):
    """Edit an existing file by replacing an exact, unique substring.

    old_string must match the file's current content exactly and, unless
    replace_all is true, must appear exactly once. Preferred over write_file
    for modifying existing files, since unrelated parts of the file are left
    untouched.
    """
    try:
        path = resolve_path(file_path, unique_basename=False)
        active_policy().check_write(path, current_workspace())

        if not path.exists():
            return {"error": "File not found"}

        if old_string == new_string:
            return {
                "error": (
                    "No-op edit refused: old_string and new_string are identical. "
                    "Make a real code change based on the current file contents."
                )
            }

        if path.stat().st_size > MAX_EDIT_FILE_SIZE:
            return {"error": "File exceeds maximum editable size."}

        text = path.read_text(encoding="utf-8", errors="ignore")
        count = text.count(old_string)

        if count == 0:
            return {
                "error": "old_string not found in file. Re-read the file and try again with an exact match."
            }

        if count > 1 and not replace_all:
            return {
                "error": f"old_string is not unique ({count} occurrences). "
                "Provide more surrounding context, or set replace_all=true "
                "to replace every occurrence."
            }

        new_text = (
            text.replace(old_string, new_string)
            if replace_all
            else text.replace(old_string, new_string, 1)
        )

        if dry_run:
            # Produce a unified diff preview instead of modifying the file.
            diff = "\n".join(
                difflib.unified_diff(
                    text.splitlines(),
                    new_text.splitlines(),
                    fromfile=str(path),
                    tofile=str(path) + " (edited)",
                    lineterm="",
                )
            )
            logger.debug("edit_file dry_run: preview diff for %s", path)
            return {
                "status": "dry_run",
                "action": "edit",
                "path": relative(path),
                "occurrences_replaced": count if replace_all else 1,
                "diff": diff,
            }

        path.write_text(new_text, encoding="utf-8")

        return {
            "status": "edited",
            "path": relative(path),
            "occurrences_replaced": count if replace_all else 1,
        }
    except Exception as e:
        logger.exception("edit_file: unexpected error editing %s", file_path)
        return {"error": str(e)}


_DEF_OR_CLASS = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


def _fuzzy_locate(text: str, old_string: str) -> dict | None:
    """Find the best approximate location of `old_string` in `text`.

    Returns a dict with 0-based line range ``start``/``end``, the matched
    region text, and a 0..1 ``confidence``, or None when nothing is close.
    Matching is line-based (line counts must agree) and strips leading/trailing
    whitespace per line so small indentation or spacing drift still matches.
    When two distinct regions score within 0.05 of each other the match is
    ambiguous and None is returned -- silently editing the wrong location is
    worse than asking for more context.

    A hard safety rule: when `old_string` names a def/class (``def name(`` or
    ``class name``), any candidate window whose matching line names a
    *different* identifier is rejected outright. A fuzzy match must never
    rename a function or class; that corrupts call sites and is the classic
    small-model edit failure (matching ``cancel_job`` onto ``_watch_job``).
    """
    old_lines = old_string.splitlines()
    if not old_lines or len(old_lines) > len(text.splitlines(keepends=True)):
        return None
    text_lines = text.splitlines()
    n = len(old_lines)
    if len(text_lines) < n:
        return None

    def ratio(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio()

    def identifiers(line: str) -> set[str]:
        match = _DEF_OR_CLASS.match(line)
        return {match.group(1)} if match else set()

    old_stripped = [line.strip() for line in old_lines]
    old_ids = [identifiers(line) for line in old_lines]
    old_joined = "\n".join(old_stripped)
    scored: list[tuple[float, int]] = []
    for start in range(len(text_lines) - n + 1):
        window = [line.strip() for line in text_lines[start : start + n]]
        if window == old_stripped:
            scored.append((1.0, start))
            continue
        # Cheap gate: the first line must resemble the target's first line.
        if ratio(text_lines[start], old_lines[0]) < 0.70:
            continue
        # Safety gate: the window must not rename any def/class the target
        # names. A differing identifier means this is not the intended symbol.
        if any(
            target and target != identifiers(window[index])
            for index, target in enumerate(old_ids)
            if target
        ):
            continue
        score = difflib.SequenceMatcher(None, "\n".join(window), old_joined).ratio()
        if score >= 0.60:
            scored.append((score, start))

    if not scored:
        return None
    scored.sort(reverse=True)
    best_score, best_start = scored[0]
    if len(scored) > 1:
        _, second_start = scored[1]
        if second_start != best_start and best_score - scored[1][0] < 0.05:
            return None
    end = best_start + n
    keepends = text.splitlines(keepends=True)
    return {
        "start": best_start,
        "end": end,
        "matched_text": "".join(keepends[best_start:end]),
        "confidence": round(best_score, 3),
    }


@tool
def apply_patch(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    dry_run: bool = False,
):
    """Apply an edit, tolerating small mismatches in old_string.

    Tries edit_file's exact unique substring match first. If the exact text
    is absent, finds the closest line-aligned region (fuzzy) and replaces it,
    reporting the confidence and the actual matched text so the model can
    verify it edited the intended location. Refuses ambiguous matches instead
    of guessing.
    """
    try:
        path = resolve_path(file_path, unique_basename=False)
        active_policy().check_write(path, current_workspace())

        if not path.exists():
            return {"error": "File not found"}

        if old_string == new_string:
            return {
                "error": (
                    "No-op patch refused: old_string and new_string are identical. "
                    "Make a real code change based on the current file contents."
                )
            }

        if path.stat().st_size > MAX_EDIT_FILE_SIZE:
            return {"error": "File exceeds maximum editable size."}

        text = path.read_text(encoding="utf-8", errors="ignore")
        count = text.count(old_string)

        match_info = {"match": "exact"}
        new_text = text
        if count == 1 or (count > 1 and replace_all):
            new_text = (
                text.replace(old_string, new_string)
                if replace_all
                else text.replace(old_string, new_string, 1)
            )
            match_info["occurrences_replaced"] = count if replace_all else 1
        elif count > 1:
            return {
                "error": f"old_string is not unique ({count} occurrences). "
                "Provide more surrounding context, or set replace_all=true "
                "to replace every occurrence."
            }
        else:
            located = _fuzzy_locate(text, old_string)
            if located is None:
                return {
                    "error": "old_string not found in file and no close match "
                    "was located. Re-read the file and retry with an exact "
                    "match or additional surrounding context."
                }
            keepends = text.splitlines(keepends=True)
            head = "".join(keepends[: located["start"]])
            tail = "".join(keepends[located["end"] :])
            replacement = new_string
            if replacement:
                # A code edit replacing a line usually keeps the line's
                # indentation; inherit it when the model guessed content
                # without leading whitespace.
                first = keepends[located["start"]]
                indent = first[: len(first) - len(first.lstrip())]
                first_repl = (
                    replacement.splitlines()[0]
                    if replacement.splitlines()
                    else replacement
                )
                if indent and not first_repl[:1].isspace():
                    replacement = indent + replacement
            if (
                replacement
                and located["end"] > located["start"]
                and keepends[located["end"] - 1].endswith("\n")
                and not replacement.endswith("\n")
            ):
                replacement += "\n"
            new_text = head + replacement + tail
            match_info = {
                "match": "fuzzy",
                "confidence": located["confidence"],
                "matched_text": located["matched_text"],
            }

        if dry_run:
            diff = "\n".join(
                difflib.unified_diff(
                    text.splitlines(),
                    new_text.splitlines(),
                    fromfile=str(path),
                    tofile=str(path) + " (applied)",
                    lineterm="",
                )
            )
            return {
                "status": "dry_run",
                "action": "patch",
                "path": relative(path),
                "diff": diff,
                **match_info,
            }

        path.write_text(new_text, encoding="utf-8")
        return {"status": "applied", "path": relative(path), **match_info}
    except Exception as e:
        logger.exception("apply_patch: unexpected error editing %s", file_path)
        return {"error": str(e)}


@tool
def run_command(command: str, directory: str = "."):
    """Run a single allowlisted shell command inside the active workspace.

    Only single commands are supported -- no pipes, redirects, subshells, or
    chaining (&&, ||, ;, |, >, <, backticks, $()). The executable must be one
    of the administrator-approved commands (see ALLOWED_COMMANDS). Each call
    uses a fresh shell; activation and other shell state do not persist.
    """
    try:
        forbidden = ["&&", "||", "|", ";", ">", "<", "`", "$("]
        if any(token in command for token in forbidden):
            return {"error": "Command chaining/redirection is not permitted."}

        parts = shlex.split(command)
        if not parts:
            return {"error": "Empty command."}

        active_policy().check_command(parts[0])

        if parts[0] not in ALLOWED_COMMANDS:
            return {
                "error": f"'{parts[0]}' is not an approved command. "
                f"Approved commands: {', '.join(ALLOWED_COMMANDS)}"
            }

        cwd = resolve_path(directory)
        if not cwd.is_dir():
            return {"error": "directory is not a directory."}

        return _run_in_isolated_runner(
            command, cwd, tier="network" if RUN_COMMANDS_ALLOW_NETWORK else "isolated"
        )
    except RunCancelled:
        raise
    except FileNotFoundError:
        return {
            "error": f"Executable not found: {command.split()[0] if command.split() else command}"
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def run_tests(
    kind: str = "pytest",
    directory: str = ".",
    coverage_target: str = "",
    test_path: str = "",
):
    """Run a small approved test command in the active workspace.

    Supported kinds are pytest, pytest_coverage, python_compile, npm_test, and
    ruff. Coverage uses the runner image's declared pytest-cov dependency.
    """
    commands: dict[str, list[str]] = {
        "pytest": ["pytest", "-q"],
        "python_compile": ["python", "-m", "compileall", "-q", "."],
        "npm_test": ["npm", "test", "--", "--runInBand"],
        "ruff": ["ruff", "check", "."],
    }
    if test_path:
        if kind not in {"pytest", "ruff"}:
            return {
                "error": "test_path is supported only for kind=pytest or kind=ruff."
            }
        normalized_target = test_path.strip()
        if not re.fullmatch(r"[A-Za-z0-9_./:\[\]-]+", normalized_target):
            return {"error": "test_path contains unsupported characters."}
        file_part, separator, node_id = normalized_target.partition("::")
        if kind == "ruff" and separator:
            return {
                "error": "ruff test_path must name a Python file, not a pytest node."
            }
        if not file_part or file_part.startswith("-"):
            return {"error": "test_path must name a workspace test file."}
        target_file = resolve_path(file_part)
        if not target_file.is_file():
            return {"error": "test_path does not name a file."}
        if kind == "pytest" and not (
            target_file.name.startswith("test_")
            or target_file.name.endswith("_test.py")
        ):
            return {
                "error": (
                    "pytest test_path must name a test module (test_*.py or "
                    "*_test.py), not an application source file. Use kind=ruff "
                    "for source-file static checks or locate the relevant test."
                )
            }
        focused_target = relative(target_file)
        if separator:
            focused_target += f"::{node_id}"
        if kind == "pytest":
            commands["pytest"] = ["pytest", "-q", focused_target]
        else:
            if target_file.suffix != ".py":
                return {"error": "ruff test_path must name a Python file."}
            commands["ruff"] = ["ruff", "check", focused_target]
    try:
        active_policy().check_command(commands[kind][0])
    except Exception as e:
        return {"error": str(e)}

    if kind == "pytest_coverage":
        target = coverage_target.strip()
        if not target:
            root = resolve_path(directory)
            target = "app" if (root / "app").is_dir() else "src"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", target):
            return {"error": "coverage_target must be a dotted Python package name."}
        commands[kind] = [
            "pytest",
            "-q",
            f"--cov={target}",
            "--cov-report=term-missing",
        ]
    if kind not in commands:
        return {"error": f"Unsupported test kind: {kind}"}
    try:
        cwd = resolve_path(directory)
        # The model commonly passes the file under investigation (e.g.
        # server/app/runner.py) as directory. Run from its parent folder so
        # "run the tests for this file" resolves instead of erroring.
        if not cwd.is_dir():
            cwd = cwd.parent
        if not cwd.is_dir():
            return {"error": "Test directory is not a directory."}
        result = _run_in_isolated_runner(
            shlex.join(commands[kind]),
            cwd,
            tier="network" if RUN_COMMANDS_ALLOW_NETWORK else "isolated",
        )
        return {"kind": kind, **result}
    except RunCancelled:
        raise
    except FileNotFoundError:
        return {"error": f"Required executable for {kind} is not installed."}
    except Exception as e:
        return {"error": str(e)}


# Async helper wrappers for non-blocking callers
async def read_file_async(file_path: str):
    """Async wrapper around `read_file` using a thread executor."""
    return await asyncio.to_thread(read_file, file_path)


async def write_file_async(
    file_path: str, content: str, overwrite: bool = False, dry_run: bool = False
):
    """Async wrapper around `write_file`."""
    return await asyncio.to_thread(write_file, file_path, content, overwrite, dry_run)


async def edit_file_async(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    dry_run: bool = False,
):
    """Async wrapper around `edit_file`."""
    return await asyncio.to_thread(
        edit_file, file_path, old_string, new_string, replace_all, dry_run
    )
