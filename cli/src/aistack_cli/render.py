"""Human and machine-readable rendering for durable run events."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from typing import Any, TextIO

MAX_TEXT_EVENT_CHARS = max(
    1_024,
    int(os.getenv("AISTACK_MAX_TEXT_EVENT_CHARS", "200000")),
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_QUIET_READ_TOOLS = {
    "find_file",
    "inspect_files",
    "inspect_test_environment",
    "list_files",
    "project_summary",
    "read_file",
    "search_text",
    "tree",
    "workspace_root",
}


def _tool_result_failed(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("error") or result.get("tool_error"):
        return True
    exit_code = result.get("exit_code")
    return (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code != 0
    )


def _sanitize_terminal(text: str) -> str:
    """Make untrusted model/tool text inert without damaging Unicode."""

    return _CONTROL_CHARACTERS.sub(
        lambda match: f"\\x{ord(match.group(0)):02x}",
        text,
    )


def _compact(value: Any, limit: int = 320) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, default=str, ensure_ascii=False)
    text = " ".join(_sanitize_terminal(text).split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _bounded_text(text: str, limit: int = MAX_TEXT_EVENT_CHARS) -> str:
    safe = _sanitize_terminal(text)
    if len(safe) <= limit:
        return safe
    omitted = len(safe) - limit
    return f"{safe[:limit]}\n… output truncated ({omitted} characters omitted)"


class EventRenderer:
    def __init__(
        self,
        *,
        output: str = "text",
        stream: TextIO | None = None,
        color: bool | None = None,
    ):
        self.output = output
        self.stream = stream or sys.stdout
        self.color = self.stream.isatty() if color is None else color
        self.width = shutil.get_terminal_size((100, 24)).columns if self.color else 100
        self.emitted_output = False
        self.terminal_status_rendered = False
        self.working_rendered = False
        self.diff: str | None = None

    def _style(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _line(self, text: str = "", *, style: str | None = None) -> None:
        """Write one safe line, applying only renderer-owned terminal styling.

        Sanitizing after ``_style`` escaped our own ANSI sequences and printed
        strings such as ``\x1b[2m`` literally.  Keep untrusted content inert
        first, then add the small SGR sequence selected by the renderer.
        """

        safe = _bounded_text(text)
        print(
            self._style(safe, style) if style else safe,
            file=self.stream,
            flush=True,
        )

    def _prefixed_line(self, prefix: str, style: str, suffix: str) -> None:
        """Render a trusted styled prefix followed by sanitized event data."""

        safe_prefix = _bounded_text(prefix)
        safe_suffix = _bounded_text(suffix)
        print(
            self._style(safe_prefix, style) + safe_suffix,
            file=self.stream,
            flush=True,
        )

    def render(self, event: dict[str, Any]) -> None:
        if self.output == "stream-json":
            self._line(json.dumps(event, default=str, ensure_ascii=False))
            return
        if self.output != "text":
            return

        kind = str(event.get("event_type", "event"))
        payload = event.get("payload") or {}
        if payload.get("prefetch") and kind in {"tool_call", "tool_result"}:
            return
        if kind == "output_delta":
            content = _bounded_text(str(payload.get("content", "")))
            if content:
                print(content, end="", file=self.stream, flush=True)
                self.emitted_output = True
            return
        if kind in {
            "queued",
            "run_started",
            "sandbox_creating",
            "sandbox_ready",
            "planning",
            "plan_ready",
            "step_started",
        }:
            if not self.working_rendered:
                self._line("• Working…", style="2")
                self.working_rendered = True
        elif kind == "tool_call":
            if payload.get("tool") in _QUIET_READ_TOOLS:
                return
            self._prefixed_line(
                f"→ {payload.get('tool', 'tool')}",
                "33",
                f" {_compact(payload.get('args', {}))}",
            )
        elif kind == "tool_result":
            if payload.get("tool") in _QUIET_READ_TOOLS and not _tool_result_failed(
                payload.get("result")
            ):
                return
            self._prefixed_line(
                f"← {payload.get('tool', 'tool')}",
                "32",
                f" {_compact(payload.get('result', ''))}",
            )
        elif kind == "diff_ready":
            self.diff = _bounded_text(str(payload.get("diff", "")))
            self._line()
            self._line("Pending diff", style="35;1")
            self._line(self.diff.rstrip())
        elif kind == "run_completed":
            answer = _bounded_text(str(payload.get("answer", "")))
            if self.emitted_output:
                self._line()
            elif answer:
                self._line(answer)
                self.emitted_output = True
            if payload.get("has_pending_diff"):
                self._line("Run is awaiting diff approval.", style="35")
        elif kind == "run_failed":
            self._line(f"Run failed: {payload.get('error', '')}", style="31")
            self.terminal_status_rendered = True
        elif kind == "run_cancelled":
            self._line("Run cancelled.", style="31")
            self.terminal_status_rendered = True

    def render_run(self, run: dict[str, Any]) -> None:
        if self.output == "json":
            self._line(json.dumps(run, default=str, ensure_ascii=False))
            return
        if self.output != "text":
            return
        if (
            run.get("status") == "failed"
            and run.get("error")
            and not self.terminal_status_rendered
        ):
            self._line(f"Run failed: {run['error']}", style="31")
        elif not self.emitted_output and run.get("answer"):
            self._line(_bounded_text(str(run["answer"])))
