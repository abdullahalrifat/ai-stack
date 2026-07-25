import os
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from langchain.tools import tool


# ============================================================
# Configuration
# ============================================================

DEFAULT_WORKSPACE = Path(
    os.getenv(
        "WORKSPACE_DIR",
        "/workspace"
    )
).resolve()


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
}


MAX_FILE_SIZE = 100_000


# ============================================================
# Helpers
# ============================================================

def validate_workspace(path: str) -> Path:
    new_path = Path(path).resolve()
    if not new_path.exists():
        raise ValueError(
            f"Workspace does not exist: {new_path}"
        )


    if (
        new_path != DEFAULT_WORKSPACE
        and DEFAULT_WORKSPACE not in new_path.parents
    ):
        raise PermissionError(
            "Workspace must be inside mounted workspace"
        )


    if not new_path.is_dir():
        raise ValueError(f"Workspace is not a directory: {new_path}")
    return new_path


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
    Resolve a path relative to the workspace.

    Supports both:
        docker-compose.yml
        agents/app/main.py
        /workspace/docker-compose.yml
    """

    p = Path(path)

    if not p.is_absolute():
        p = current_workspace() / p

    p = p.resolve()

    workspace = current_workspace()
    if p != workspace and workspace not in p.parents:
        raise PermissionError(
            "Access outside workspace denied."
        )

    return p


def ignored(path: Path) -> bool:

    return any(
        part in IGNORE_DIRS
        for part in path.parts
    )


def relative(path: Path):

    return str(
        path.relative_to(current_workspace())
    )


# ============================================================
# Workspace
# ============================================================

@tool
def workspace_root():
    """
    Return the mounted workspace location.
    """

    return {
        "workspace": str(current_workspace())
    }


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

            entries = sorted(
                path.iterdir(),
                key=lambda x: (
                    x.is_file(),
                    x.name.lower(),
                ),
            )

            for entry in entries:

                if ignored(entry):
                    continue

                output.append(
                    "  " * level +
                    entry.name
                )

                if entry.is_dir():

                    walk(
                        entry,
                        level + 1,
                    )

        walk(root, 0)

        return "\n".join(output)

    except Exception as e:

        return {
            "error": str(e)
        }


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

        for file in sorted(path.iterdir()):

            if ignored(file):
                continue

            files.append(
                {
                    "name": file.name,
                    "path": relative(file),
                    "type":
                        "directory"
                        if file.is_dir()
                        else "file",
                }
            )

        return files

    except Exception as e:

        return {
            "error": str(e)
        }


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

            return {
                "error":
                    "File not found"
            }

        if path.stat().st_size > MAX_FILE_SIZE:

            return {
                "error":
                    "File exceeds maximum size."
            }

        return path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

    except Exception as e:

        return {
            "error": str(e)
        }


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

        for file in current_workspace().rglob("*"):

            if ignored(file):
                continue

            if filename.lower() in file.name.lower():

                matches.append(
                    relative(file)
                )

        return matches[:100]

    except Exception as e:

        return {
            "error": str(e)
        }


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

        for file in root.rglob("*"):

            if ignored(file):
                continue

            if not file.is_file():
                continue

            try:

                text = file.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )

            except Exception:
                continue

            if keyword.lower() in text.lower():

                matches.append(
                    relative(file)
                )

        return matches[:100]

    except Exception as e:

        return {
            "error": str(e)
        }


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

    for file in current_workspace().rglob("*"):

        if ignored(file):
            continue

        if not file.is_file():
            continue

        ext = file.suffix.lower()

        extensions[ext] = (
            extensions.get(ext, 0) + 1
        )

        if file.name in {
            "docker-compose.yml",
            "Dockerfile",
            "README.md",
            "requirements.txt",
            "pyproject.toml",
            "package.json",
            ".env.example",
        }:

            important.append(
                relative(file)
            )

    return {
        "workspace": str(current_workspace()),
        "important_files": important,
        "languages": extensions,
    }

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

        for item in paths:

            path = resolve_path(item)

            if not path.exists():

                results.append(
                    {
                        "path": item,
                        "error": "Not found"
                    }
                )

                continue


            if path.is_dir():

                files = []

                for f in path.iterdir():

                    if ignored(f):
                        continue

                    files.append(
                        f.name
                    )

                results.append(
                    {
                        "path": item,
                        "type": "directory",
                        "contents": files[:100]
                    }
                )

            else:

                if path.stat().st_size > MAX_FILE_SIZE:

                    results.append(
                        {
                            "path": item,
                            "error":
                                "File too large"
                        }
                    )

                    continue


                results.append(
                    {
                        "path": item,
                        "type": "file",
                        "content":
                            path.read_text(
                                encoding="utf-8",
                                errors="ignore"
                            )
                    }
                )


        return results


    except Exception as e:

        return {
            "error": str(e)
        }


# ============================================================
# Controlled coding tools
# ============================================================

@tool
def write_file(file_path: str, content: str, overwrite: bool = False):
    """Create a UTF-8 text file in the active workspace.

    This tool is deliberately small: it cannot access paths outside the
    request-scoped workspace and refuses to replace an existing file unless
    overwrite is explicitly true.
    """
    try:
        path = resolve_path(file_path)
        if path.exists() and not overwrite:
            return {"error": "File exists; reread it and set overwrite=true to replace it."}
        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            return {"error": "Content exceeds maximum size."}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"status": "written", "path": relative(path), "bytes": path.stat().st_size}
    except Exception as e:
        return {"error": str(e)}


@tool
def run_tests(kind: str = "pytest", directory: str = "."):
    """Run a small approved test command in the active workspace.

    Supported kinds are pytest, python_compile, and npm_test. Arbitrary shell
    commands are intentionally not exposed to the language model.
    """
    commands = {
        "pytest": ["pytest", "-q"],
        "python_compile": ["python", "-m", "compileall", "-q", "."],
        "npm_test": ["npm", "test", "--", "--runInBand"],
    }
    if kind not in commands:
        return {"error": f"Unsupported test kind: {kind}"}
    try:
        cwd = resolve_path(directory)
        if not cwd.is_dir():
            return {"error": "Test directory is not a directory."}
        result = subprocess.run(
            commands[kind], cwd=cwd, text=True, capture_output=True,
            timeout=int(os.getenv("COMMAND_TIMEOUT_SECONDS", "120")),
            check=False,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return {"kind": kind, "exit_code": result.returncode, "output": output[-30000:]}
    except subprocess.TimeoutExpired:
        return {"error": "Test command timed out."}
    except FileNotFoundError:
        return {"error": f"Required executable for {kind} is not installed."}
    except Exception as e:
        return {"error": str(e)}
