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
        self.diff: str | None = None

    def _style(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _line(self, text: str = "") -> None:
        print(_bounded_text(text), file=self.stream, flush=True)

    def render(self, event: dict[str, Any]) -> None:
        if self.output == "stream-json":
            self._line(json.dumps(event, default=str, ensure_ascii=False))
            return
        if self.output != "text":
            return

        kind = str(event.get("event_type", "event"))
        payload = event.get("payload") or {}
        if kind == "output_delta":
            content = _bounded_text(str(payload.get("content", "")))
            if content:
                print(content, end="", file=self.stream, flush=True)
                self.emitted_output = True
            return
        if kind == "step_started":
            self._line(self._style(f"• step {payload.get('step', '?')}", "2"))
        elif kind == "planning":
            self._line(self._style("• planning", "36"))
        elif kind == "plan_ready":
            plan = payload.get("plan") or []
            self._line(self._style("• plan", "36"))
            for item in plan:
                self._line(f"  - {item}")
        elif kind == "tool_call":
            self._line(
                self._style(f"→ {payload.get('tool', 'tool')}", "33")
                + f" {_compact(payload.get('args', {}))}"
            )
        elif kind == "tool_result":
            self._line(
                self._style(f"← {payload.get('tool', 'tool')}", "32")
                + f" {_compact(payload.get('result', ''))}"
            )
        elif kind == "diff_ready":
            self.diff = _bounded_text(str(payload.get("diff", "")))
            self._line()
            self._line(self._style("Pending diff", "35;1"))
            self._line(self.diff.rstrip())
        elif kind == "run_completed":
            answer = _bounded_text(str(payload.get("answer", "")))
            if self.emitted_output:
                self._line()
            elif answer:
                self._line(answer)
                self.emitted_output = True
            if payload.get("has_pending_diff"):
                self._line(self._style("Run is awaiting diff approval.", "35"))
        elif kind == "run_failed":
            self._line(self._style(f"Run failed: {payload.get('error', '')}", "31"))
        elif kind == "run_cancelled":
            self._line(self._style("Run cancelled.", "31"))
        elif kind in {"queued", "run_started", "sandbox_creating", "sandbox_ready"}:
            label = kind.replace("_", " ")
            self._line(self._style(f"• {label}", "2"))

    def render_run(self, run: dict[str, Any]) -> None:
        if self.output == "json":
            self._line(json.dumps(run, default=str, ensure_ascii=False))
            return
        if self.output != "text":
            return
        if run.get("status") == "failed" and run.get("error"):
            self._line(self._style(f"Run failed: {run['error']}", "31"))
        elif not self.emitted_output and run.get("answer"):
            self._line(_bounded_text(str(run["answer"])))
