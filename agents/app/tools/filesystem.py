import os
import importlib.util
import re
import resource
import shlex
import shutil
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from langchain.tools import tool

from app.core.config import (
    ALLOWED_COMMANDS,
    COMMAND_TIMEOUT_SECONDS,
    DEFAULT_WORKSPACE as CONFIGURED_DEFAULT_WORKSPACE,
    RUNNER_CPU_SECONDS,
    RUNNER_MAX_OPEN_FILES,
    RUNNER_MEMORY_MB,
    WORKSPACE_ROOTS,
    RUNNER_API_KEY,
    RUNNER_URL,
    SANDBOX_ROOT,
)
import requests

# ============================================================
# Configuration
# ============================================================

DEFAULT_WORKSPACE = CONFIGURED_DEFAULT_WORKSPACE


# Context-local workspace prevents one HTTP request from changing another
# request's filesystem boundary.
CURRENT_WORKSPACE: ContextVar[Path] = ContextVar(
    "current_workspace", default=DEFAULT_WORKSPACE
)


IGNORE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
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


MAX_FILE_SIZE = 100_000
MAX_SCAN_FILES = 5_000
MAX_SEARCH_FILE_SIZE = 512_000
MAX_INSPECT_PATHS = 20
MAX_DIRECTORY_ENTRIES = 1_000


def _runner_preexec() -> None:
    """Apply process-level limits inside the isolated agent container."""
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (RUNNER_CPU_SECONDS, RUNNER_CPU_SECONDS))
    memory = RUNNER_MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_SIZE * 10, MAX_FILE_SIZE * 10))
    resource.setrlimit(resource.RLIMIT_NOFILE, (RUNNER_MAX_OPEN_FILES, RUNNER_MAX_OPEN_FILES))


def _run_limited(parts: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/tmp/agent-runner",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        parts,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
        env=safe_env,
        preexec_fn=_runner_preexec,
    )


def _run_in_isolated_runner(command: str, cwd: Path) -> dict:
    if SANDBOX_ROOT not in cwd.parents:
        return {"error": "Commands may run only inside a disposable sandbox worktree."}
    try:
        response = requests.post(
            f"{RUNNER_URL}/execute", json={"command": command, "directory": str(cwd)},
            headers={"X-Runner-Key": RUNNER_API_KEY or ""}, timeout=COMMAND_TIMEOUT_SECONDS + 5,
        )
        if not response.ok:
            return {"error": "Isolated runner rejected command."}
        payload = response.json()
        return {"command": command, "exit_code": payload["exit_code"], "output": payload.get("output", "")}
    except requests.RequestException:
        return {"error": "Isolated runner is unavailable."}


# ============================================================
# Helpers
# ============================================================


def validate_workspace(path: str) -> Path:
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

    allowed = any(
        new_path == root or root in new_path.parents for root in WORKSPACE_ROOTS
    )

    if not allowed:
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
def workspace_context(path: str):
    token = CURRENT_WORKSPACE.set(validate_workspace(path))
    try:
        yield
    finally:
        CURRENT_WORKSPACE.reset(token)


def current_workspace() -> Path:
    return CURRENT_WORKSPACE.get()


def resolve_path(path: str) -> Path:
    """
    Resolve a path relative to the current workspace.

    Supports both:
        docker-compose.yml
        agents/app/main.py
        /workspace/docker-compose.yml
    """

    p = Path(path)

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

    p = p.resolve()

    workspace = current_workspace()
    if p != workspace and workspace not in p.parents:
        raise PermissionError("Access outside workspace denied.")

    return p


def ignored(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts)


def relative(path: Path):
    return str(path.relative_to(current_workspace()))


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
        path = resolve_path(directory)

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
):
    """
    Read a UTF-8 text file.
    """

    try:
        path = resolve_path(file_path)

        if not path.exists():
            return {"error": "File not found"}

        if path.stat().st_size > MAX_FILE_SIZE:
            return {"error": "File exceeds maximum size."}

        return path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

    except Exception as e:
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

        for index, file in enumerate(current_workspace().rglob("*")):
            if index >= MAX_SCAN_FILES:
                break
            if ignored(file):
                continue

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

        for index, file in enumerate(root.rglob("*")):
            if index >= MAX_SCAN_FILES:
                break
            if ignored(file):
                continue

            if not file.is_file():
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
        for index, file in enumerate(current_workspace().rglob("*")):
            if index >= MAX_SCAN_FILES:
                break
            if ignored(file) or not file.is_file():
                continue

            ext = file.suffix.lower()
            extensions[ext] = extensions.get(ext, 0) + 1

            if file.name in {
                "docker-compose.yml",
                "Dockerfile",
                "README.md",
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
        return {
            "directory": relative(root),
            "configs": configs,
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
        for item in paths[:MAX_INSPECT_PATHS]:
            path = resolve_path(item)

            if not path.exists():
                results.append({"path": item, "error": "Not found"})
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

                results.append(
                    {
                        "path": item,
                        "type": "file",
                        "content": path.read_text(encoding="utf-8", errors="ignore")[:MAX_FILE_SIZE],
                    }
                )

        return {"items": results, "truncated": len(paths) > MAX_INSPECT_PATHS}

    except Exception as e:
        return {"error": str(e)}


# ============================================================
# Controlled coding tools
# ============================================================


@tool
def write_file(file_path: str, content: str, overwrite: bool = False):
    """Create a UTF-8 text file in the active workspace.

    This tool is deliberately small: it cannot access paths outside the
    request-scoped workspace and refuses to replace an existing file unless
    overwrite is explicitly true. Prefer edit_file for modifying existing
    files.
    """
    try:
        path = resolve_path(file_path)
        if path.exists() and not overwrite:
            return {
                "error": "File exists; reread it and set overwrite=true to replace it."
            }
        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            return {"error": "Content exceeds maximum size."}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "status": "written",
            "path": relative(path),
            "bytes": path.stat().st_size,
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
):
    """Edit an existing file by replacing an exact, unique substring.

    old_string must match the file's current content exactly and, unless
    replace_all is true, must appear exactly once. Preferred over write_file
    for modifying existing files, since unrelated parts of the file are left
    untouched.
    """
    try:
        path = resolve_path(file_path)

        if not path.exists():
            return {"error": "File not found"}

        if path.stat().st_size > MAX_FILE_SIZE:
            return {"error": "File exceeds maximum size."}

        text = path.read_text(encoding="utf-8", errors="ignore")
        count = text.count(old_string)

        if count == 0:
            return {
                "error": "old_string not found in file. Re-read the file and "
                "try again with an exact match."
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

        path.write_text(new_text, encoding="utf-8")

        return {
            "status": "edited",
            "path": relative(path),
            "occurrences_replaced": count if replace_all else 1,
        }
    except Exception as e:
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

        if parts[0] not in ALLOWED_COMMANDS:
            return {
                "error": f"'{parts[0]}' is not an approved command. "
                f"Approved commands: {', '.join(ALLOWED_COMMANDS)}"
            }

        cwd = resolve_path(directory)
        if not cwd.is_dir():
            return {"error": "directory is not a directory."}

        return _run_in_isolated_runner(command, cwd)
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out."}
    except FileNotFoundError:
        return {"error": f"Executable not found: {command.split()[0] if command.split() else command}"}
    except Exception as e:
        return {"error": str(e)}


@tool
def run_tests(
    kind: str = "pytest",
    directory: str = ".",
    coverage_target: str = "",
):
    """Run a small approved test command in the active workspace.

    Supported kinds are pytest, pytest_coverage, python_compile, npm_test, and
    ruff. Coverage uses the runner image's declared pytest-cov dependency.
    """
    commands = {
        "pytest": ["pytest", "-q"],
        "python_compile": ["python", "-m", "compileall", "-q", "."],
        "npm_test": ["npm", "test", "--", "--runInBand"],
        "ruff": ["ruff", "check", "."],
    }
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
        if not cwd.is_dir():
            return {"error": "Test directory is not a directory."}
        result = _run_in_isolated_runner(" ".join(commands[kind]), cwd)
        return {"kind": kind, **result}
    except subprocess.TimeoutExpired:
        return {"error": "Test command timed out."}
    except FileNotFoundError:
        return {"error": f"Required executable for {kind} is not installed."}
    except Exception as e:
        return {"error": str(e)}
