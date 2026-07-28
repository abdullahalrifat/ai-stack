"""Human and machine-readable rendering for durable run events."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO


def _compact(value: Any, limit: int = 320) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, default=str, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


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
        self.emitted_output = False
        self.diff: str | None = None

    def _style(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _line(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    def render(self, event: dict[str, Any]) -> None:
        if self.output == "stream-json":
            self._line(json.dumps(event, default=str, ensure_ascii=False))
            return
        if self.output != "text":
            return

        kind = str(event.get("event_type", "event"))
        payload = event.get("payload") or {}
        if kind == "output_delta":
            content = str(payload.get("content", ""))
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
            self.diff = str(payload.get("diff", ""))
            self._line()
            self._line(self._style("Pending diff", "35;1"))
            self._line(self.diff.rstrip())
        elif kind == "run_completed":
            answer = str(payload.get("answer", ""))
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
            self._line(str(run["answer"]))
