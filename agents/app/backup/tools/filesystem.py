import os
from pathlib import Path

from langchain.tools import tool


# ============================================================
# Configuration
# ============================================================

WORKSPACE = Path(
    os.getenv(
        "WORKSPACE_DIR",
        "/workspace"
    )
).resolve()


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
        p = WORKSPACE / p

    p = p.resolve()

    if p != WORKSPACE and WORKSPACE not in p.parents:
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
        path.relative_to(WORKSPACE)
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
        "workspace": str(WORKSPACE)
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

        for file in WORKSPACE.rglob("*"):

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

    for file in WORKSPACE.rglob("*"):

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
        "workspace": str(WORKSPACE),
        "important_files": important,
        "languages": extensions,
    }