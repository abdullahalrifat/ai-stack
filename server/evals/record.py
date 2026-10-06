"""Record a golden agent trace from a live run against the stack.

A golden trace is captured from a real run's event stream (prefetch tool
calls) and checkpoint transcript (the model's turns and tool results), and
saved as JSON so the offline replay harness can verify the machinery still
produces the same tool-call sequence (``evals/replay.py``).

Usage:
    python evals/record.py --base http://127.0.0.1:8000 --key "$AGENT_API_KEY" \
        --prompt "Review this repository for reliability issues." --model qwen3:4b \
        --output golden/my-trace.json
"""

from __future__ import annotations

import argparse
import json
import sys

import requests

from . import traces as traces_module


def _stream_events(base: str, key: str, run_id: str, timeout_seconds: int = 600):
    headers = {"Authorization": f"Bearer {key}"}
    response = requests.get(
        f"{base.rstrip('/')}/runs/{run_id}/events",
        headers=headers,
        stream=True,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    events = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data: "):
            continue
        try:
            event = json.loads(raw_line[len("data: ") :])
        except json.JSONDecodeError:
            continue
        events.append(event)
    return events


def record(
    *,
    base: str,
    key: str,
    prompt: str,
    model: str,
    workspace: str | None = None,
    allow_write: bool = False,
    timeout_seconds: int = 600,
) -> dict:
    headers = {"Authorization": f"Bearer {key}"}
    payload = {
        "task": prompt,
        "model": model,
        "allow_write": allow_write,
    }
    if workspace:
        payload["workspace"] = workspace

    created = requests.post(
        f"{base.rstrip('/')}/runs", headers=headers, json=payload, timeout=30
    )
    created.raise_for_status()
    run_id = created.json()["run_id"]

    events = _stream_events(base, key, run_id, timeout_seconds)
    run = requests.get(
        f"{base.rstrip('/')}/runs/{run_id}", headers=headers, timeout=30
    ).json()

    checkpoint = run.get("checkpoint") or {}
    messages = checkpoint.get("messages") or []
    if not messages:
        raise RuntimeError(f"Run {run_id} has no transcript in its checkpoint")

    prefetch_calls = _prefetch_calls_from_events(events)

    trace = traces_module.trace_from_transcript(
        messages,
        run_id=run_id,
        prompt=prompt,
        model=model,
        model=run.get("model") or profile,
        workspace=workspace or run.get("requested_workspace") or ".",
        allow_write=allow_write,
        plan=list(checkpoint.get("plan") or []),
        answer=run.get("answer") or "",
        steps=int(checkpoint.get("steps") or 0),
        partial=bool(run.get("partial")),
        successful_mutation=bool(checkpoint.get("successful_mutation")),
        successful_verification=bool(checkpoint.get("successful_verification")),
        prefetch_calls=prefetch_calls,
    )
    return trace


def _prefetch_calls_from_events(events: list[dict]) -> list[dict]:
    """Recover prefetch tool executions from the ordered event stream.

    The executor emits a ``tool_call`` then a ``tool_result`` event for each
    prefetch invocation, each flagged ``prefetch: true``. Pair them by order
    to recover tool, arguments, and result.
    """

    calls = [e for e in events if e.get("event_type") == "tool_call"]
    results = [e for e in events if e.get("event_type") == "tool_result"]
    prefetch_calls = []
    for index, event in enumerate(calls):
        payload = event.get("payload") or {}
        if not payload.get("prefetch"):
            continue
        result_payload = results[index].get("payload") or {}
        prefetch_calls.append(
            {
                "tool": payload.get("tool"),
                "args": payload.get("args") or {},
                "result": result_payload.get("result"),
            }
        )
    return prefetch_calls


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Agent service base URL")
    parser.add_argument("--key", required=True, help="AGENT_API_KEY value")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--output", required=True, help="Path to write the trace")
    args = parser.parse_args(argv)

    trace = record(
        base=args.base,
        key=args.key,
        prompt=args.prompt,
        model=args.model,
        workspace=args.workspace,
        allow_write=args.allow_write,
    )
    traces_module.save(trace, args.output)
    print(f"Recorded {trace['id']} ({len(trace['tool_sequence'])} tool calls)")
    print(json.dumps(trace["model_turns"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
