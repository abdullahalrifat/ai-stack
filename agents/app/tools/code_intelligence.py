"""Bounded, deterministic source intelligence for repository tasks."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from langchain.tools import tool

from .filesystem import ignored, relative, resolve_path

MAX_SOURCE_FILES = 400
MAX_FILE_BYTES = 250_000
MAX_REQUESTS = 12
MAX_RESULT_CHARS = 24_000

_STOP_WORDS = {
    "about",
    "agent",
    "codebase",
    "feature",
    "from",
    "implement",
    "implementation",
    "project",
    "review",
    "scale",
    "this",
    "todo",
    "with",
}


def _keywords(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            token
            for token in re.findall(r"[a-z][a-z0-9_]{2,}", text.casefold())
            if token not in _STOP_WORDS
        )
    )[:16]


def _python_files(directory: str = ".") -> list[Path]:
    root = resolve_path(directory)
    if not root.is_dir():
        return []
    files = []
    for path in root.rglob("*.py"):
        if len(files) >= MAX_SOURCE_FILES:
            break
        if ignored(path) or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        files.append(path)
    return files


def _parse(path: Path) -> tuple[str, ast.AST | None]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        return text, ast.parse(text)
    except SyntaxError:
        return text, None


def _symbols(tree: ast.AST | None) -> list[dict[str, Any]]:
    if tree is None:
        return []
    result = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.append(
                {
                    "name": node.name,
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                }
            )
    return sorted(result, key=lambda item: item["start_line"])


def _imports(tree: ast.AST | None) -> list[str]:
    if tree is None:
        return []
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return list(dict.fromkeys(names))[:30]


def _test_file(path: Path) -> bool:
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def _evidence_requirements(requirement: str) -> list[str]:
    lowered = requirement.casefold()
    if re.search(r"\b(?:bug|fix|repair|broken|failure)\b", lowered):
        return ["failing path", "owning symbol", "relevant tests"]
    if re.search(r"\b(?:refactor|rename|replace)\b", lowered):
        return ["callers", "dependencies", "public contracts", "relevant tests"]
    if re.search(r"\b(?:todo|roadmap|tier)\b", lowered):
        return [
            "exact checklist item",
            "current implementation",
            "missing behavior",
            "verification strategy",
        ]
    if re.search(r"\b(?:implement|add|build|create|feature)\b", lowered):
        return [
            "requirements",
            "extension points",
            "analogous implementation",
            "relevant tests",
        ]
    return ["architecture", "relevant symbols", "tests", "configuration"]


@tool
def analyze_task_context(requirement: str, directory: str = "."):
    """Build a bounded architecture, symbol, dependency, and test evidence packet."""

    try:
        keywords = _keywords(requirement)
        ranked = []
        source_files = _python_files(directory)
        for path in source_files:
            text, tree = _parse(path)
            lowered = text.casefold()
            path_text = relative(path).casefold()
            lines = text.splitlines()
            score = sum(lowered.count(word) for word in keywords)
            score += 4 * sum(word in path_text for word in keywords)
            symbol_items = _symbols(tree)
            relevant_symbols = []
            for item in symbol_items:
                name = item["name"].casefold()
                body = "\n".join(
                    lines[item["start_line"] - 1 : item["end_line"]]
                ).casefold()
                symbol_score = 4 * sum(word in name for word in keywords)
                symbol_score += sum(body.count(word) for word in keywords)
                if symbol_score:
                    relevant_symbols.append({**item, "score": symbol_score})
            relevant_symbols.sort(key=lambda item: (-item["score"], item["start_line"]))
            score += sum(item["score"] for item in relevant_symbols[:5])
            if score:
                ranked.append(
                    {
                        "path": relative(path),
                        "score": score,
                        "symbols": relevant_symbols[:12],
                        "dependencies": _imports(tree),
                        "is_test": _test_file(path),
                    }
                )
        ranked.sort(key=lambda item: (-item["score"], item["path"]))
        sources = [item for item in ranked if not item["is_test"]][:10]
        tests = [item for item in ranked if item["is_test"]][:8]
        owning_symbols = [
            {
                "path": item["path"],
                **{key: value for key, value in symbol.items() if key != "score"},
            }
            for item in sources
            for symbol in item["symbols"][:5]
        ][:20]
        return {
            "requirement": requirement.strip(),
            "keywords": keywords,
            "evidence_requirements": _evidence_requirements(requirement),
            "relevant_files": [item["path"] for item in sources],
            "test_targets": [item["path"] for item in tests],
            "owning_symbols": owning_symbols,
            "confirmed_existing": [
                f"{item['path']}::{item['name']}" for item in owning_symbols
            ],
            "confirmed_missing": [],
            "dependencies": {
                item["path"]: item["dependencies"] for item in sources[:6]
            },
            "open_questions": (
                ["Confirm the owning extension point before mutation."]
                if not owning_symbols
                else []
            ),
            "files_scanned": len(source_files),
            "truncated": len(source_files) >= MAX_SOURCE_FILES,
            "verification_strategy": (
                {"kind": "pytest", "test_path": tests[0]["path"]}
                if tests
                else {"kind": "project_default", "test_path": ""}
            ),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _symbol_excerpt(path: Path, symbol_name: str) -> dict[str, Any]:
    text, tree = _parse(path)
    lines = text.splitlines()
    matches = [item for item in _symbols(tree) if item["name"] == symbol_name]
    if not matches:
        return {"error": f"Symbol not found: {symbol_name}", "path": relative(path)}
    item = matches[0]
    return {
        "path": relative(path),
        "symbol": symbol_name,
        "kind": item["kind"],
        "start_line": item["start_line"],
        "end_line": item["end_line"],
        "content": "\n".join(lines[item["start_line"] - 1 : item["end_line"]]),
    }


@tool
def inspect_code(requests: list[dict[str, Any]]):
    """Inspect several source symbols, patterns, or line ranges in one bounded call."""

    results = []
    remaining = MAX_RESULT_CHARS
    for request in requests[:MAX_REQUESTS]:
        try:
            path = resolve_path(str(request.get("path", "")))
            if not path.is_file() or ignored(path):
                result = {
                    "error": "Path is not an inspectable file.",
                    "path": str(path),
                }
            elif request.get("symbol"):
                result = _symbol_excerpt(path, str(request["symbol"]))
            else:
                text = path.read_text(encoding="utf-8", errors="ignore")
                lines = text.splitlines()
                if request.get("pattern"):
                    regex = re.compile(str(request["pattern"]))
                    matched = [
                        index
                        for index, line in enumerate(lines, start=1)
                        if regex.search(line)
                    ][:20]
                    context = max(0, min(8, int(request.get("context_lines", 2))))
                    windows = []
                    for line_number in matched:
                        start = max(1, line_number - context)
                        end = min(len(lines), line_number + context)
                        windows.append(
                            {
                                "line": line_number,
                                "start_line": start,
                                "end_line": end,
                                "content": "\n".join(lines[start - 1 : end]),
                            }
                        )
                    result = {"path": relative(path), "matches": windows}
                else:
                    start = max(1, int(request.get("start_line", 1)))
                    end = min(
                        len(lines),
                        max(start, int(request.get("end_line", start + 119))),
                    )
                    result = {
                        "path": relative(path),
                        "start_line": start,
                        "end_line": end,
                        "content": "\n".join(lines[start - 1 : end]),
                    }
        except (OSError, ValueError, TypeError, re.error) as exc:
            result = {"error": str(exc), "request": request}
        encoded = str(result)
        if len(encoded) > remaining:
            result = {"truncated": True, "preview": encoded[: max(0, remaining)]}
        remaining -= min(len(encoded), remaining)
        results.append(result)
        if remaining <= 0:
            break
    return {
        "items": results,
        "requests": len(requests),
        "truncated": len(results) < len(requests),
    }
