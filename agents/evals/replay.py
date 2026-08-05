"""Offline deterministic replay of a golden agent trace.

``replay_trace`` re-runs a recorded run through the real executor loop with
the *model* stubbed to replay the golden model script, while tools execute
for real (or against an injected registry in tests). The resulting tool-call
sequence is compared to the golden sequence: any divergence means the agent
machinery changed behavior since the trace was recorded.

This is a machinery regression harness, not a model-quality benchmark. Model
quality checks live in ``run_evals.py`` against a live stack.

Usage:
    python evals/replay.py --trace golden/code-review.json --workspace .
The exit code is non-zero when any trace diverges.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.agent.executor import execute_plan
from app.agent.state import AgentState

from . import traces as traces_module


class ScriptExhausted(Exception):
    """Raised when the replay made more model calls than the trace script."""


def _turn_to_message(turn: dict) -> SimpleNamespace:
    """Turn a recorded model response into an OpenAI-message stand-in."""

    calls = turn.get("tool_calls")
    if calls:
        return SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id=call.get("id") or f"replay_call_{index}",
                    function=SimpleNamespace(
                        name=call["name"],
                        arguments=json.dumps(call.get("arguments") or {}),
                    ),
                )
                for index, call in enumerate(calls)
            ],
        )
    return SimpleNamespace(content=turn.get("content") or "", tool_calls=None)


def build_state(trace: dict) -> AgentState:
    state = AgentState(
        conversation_id=f"replay:{trace['id']}",
        user_message=trace["prompt"],
        workspace=trace.get("workspace") or ".",
        model=trace.get("model") or "replay-model",
        prompt_mode=trace.get("profile") or "code",
        allow_write=bool(trace.get("allow_write")),
    )
    state.plan = list(trace.get("plan") or [])
    return state


def replay_trace(
    trace: dict,
    *,
    workspace: str | None = None,
    on_event=None,
    should_cancel=None,
) -> dict:
    """Replay a golden trace and report sequence fidelity to the golden run.

    Returns a report dict with ``status`` in {"PASS", "FAIL"}, a list of
    human-readable ``reasons``, the observed vs golden tool sequences, and the
    replayed terminal answer.
    """

    import app.agent.executor as executor_module

    trace = dict(trace)
    if workspace is not None:
        trace["workspace"] = workspace

    problems = traces_module.validate(trace)
    if problems:
        return {
            "id": trace.get("id"),
            "status": "FAIL",
            "reasons": ["invalid trace"] + problems,
            "golden_tool_calls": len(trace.get("tool_sequence", [])),
            "observed_tool_calls": 0,
            "divergence": {"missing": [], "extra": [], "mismatched": []},
            "answer": None,
        }

    script = list(trace["model_turns"])
    events: list[tuple[str, dict]] = []
    observed: list[dict] = []

    def collect(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))
        if on_event is not None:
            on_event(event_type, payload)

    def scripted_model(messages, tools, **kwargs):
        if not script:
            raise ScriptExhausted(
                f"model script exhausted after {len(trace['model_turns'])} turns"
            )
        return _turn_to_message(script.pop(0))

    def scripted_plain_chat(messages, **kwargs):
        return "[offline replay: summary/synthesis response]"

    state = build_state(trace)
    state.messages = []

    golden_sequence = [
        {"tool": item["tool"], "args": item.get("args") or {}}
        for item in trace["tool_sequence"]
    ]

    real_execute = executor_module.registry.execute

    def recording_execute(tool_name, args):
        result = real_execute(tool_name, args)
        observed.append({"tool": tool_name, "args": dict(args or {})})
        return result

    try:
        with mock.patch.object(
            executor_module, "chat_with_tools", side_effect=scripted_model
        ), mock.patch.object(
            executor_module, "chat", side_effect=scripted_plain_chat
        ), mock.patch.object(
            executor_module, "_stream_message", side_effect=scripted_model
        ), mock.patch.object(
            executor_module.registry, "execute", side_effect=recording_execute
        ):
            observed_answer = execute_plan(
                state,
                on_event=collect,
                should_cancel=should_cancel or (lambda: False),
            )
    except ScriptExhausted as exc:
        return {
            "id": trace["id"],
            "status": "FAIL",
            "reasons": [str(exc)],
            "golden_tool_calls": len(golden_sequence),
            "observed_tool_calls": len(observed),
            "divergence": {"missing": [], "extra": [], "mismatched": []},
            "answer": None,
        }

    observed_sequence = [
        {"tool": item["tool"], "args": item.get("args") or {}}
        for item in observed
    ]
    divergence = _diff_sequences(golden_sequence, observed_sequence)

    reasons = []
    if divergence["missing"] or divergence["extra"] or divergence["mismatched"]:
        reasons.append("tool-call sequence diverged from the golden trace")
        if divergence["missing"]:
            reasons.append(
                "missing tool calls: "
                + ", ".join(
                    f"{item['tool']}({sorted(item['args'])})"
                    for item in divergence["missing"]
                )
            )
        if divergence["extra"]:
            reasons.append(
                "extra tool calls: "
                + ", ".join(
                    f"{item['tool']}({sorted(item['args'])})"
                    for item in divergence["extra"]
                )
            )
        if divergence["mismatched"]:
            reasons.append(
                "argument mismatches: "
                + "; ".join(
                    f"{item['tool']}: expected {sorted(golden['args'])} got {sorted(actual['args'])}"
                    for item, golden, actual in divergence["mismatched"]
                )
            )
    if not state.finished:
        reasons.append("executor did not finish the run")
    if not reasons:
        status = "PASS"
    else:
        status = "FAIL"

    return {
        "id": trace["id"],
        "status": status,
        "reasons": reasons,
        "golden_tool_calls": len(golden_sequence),
        "observed_tool_calls": len(observed_sequence),
        "divergence": {
            "missing": divergence["missing"],
            "extra": divergence["extra"],
            "mismatched": [
                {
                    "tool": item["tool"],
                    "expected_args": golden["args"],
                    "actual_args": actual["args"],
                }
                for item, golden, actual in divergence["mismatched"]
            ],
        },
        "answer": observed_answer,
        "partial": bool(getattr(state, "partial", False)),
        "steps": int(getattr(state, "steps", 0)),
        "events": events,
    }


def _diff_sequences(golden, observed) -> dict:
    """Order-sensitive diff of two tool-call sequences (tool + args keys)."""

    missing: list[dict] = []
    extra: list[dict] = []
    mismatched: list[tuple[dict, dict, dict]] = []
    observed_by_position = {position: item for position, item in enumerate(observed)}
    golden_positions = {position: item for position, item in enumerate(golden)}
    for position in range(max(len(golden), len(observed))):
        golden_item = golden_positions.get(position)
        observed_item = observed_by_position.get(position)
        if golden_item is None:
            extra.append(observed_item)
            continue
        if observed_item is None:
            missing.append(golden_item)
            continue
        if golden_item["tool"] != observed_item["tool"]:
            missing.append(golden_item)
            extra.append(observed_item)
            continue
        if golden_item["args"] != observed_item["args"]:
            mismatched.append((golden_item, golden_item, observed_item))
    return {"missing": missing, "extra": extra, "mismatched": mismatched}


def replay_file(path: str, *, workspace: str | None = None) -> dict:
    return replay_trace(
        traces_module.load(path),
        workspace=workspace,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace", required=True, help="Path to a golden trace JSON file"
    )
    parser.add_argument("--workspace", default=None, help="Override workspace")
    args = parser.parse_args(argv)

    report = replay_file(args.trace, workspace=args.workspace)
    print(f"{report['status']} {report['id']}")
    for reason in report["reasons"]:
        print(f"  - {reason}")
    print(
        f"  golden tool calls: {report['golden_tool_calls']}, "
        f"observed: {report['observed_tool_calls']}, steps: {report['steps']}"
    )
    if report["status"] == "FAIL":
        print(json.dumps(report, indent=2, default=str))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
