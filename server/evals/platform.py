"""Replayable Server evaluation runner using jarvis-core contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jarvis_core import EvalCase, run_evals


def load_cases(path: str) -> list[EvalCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else payload.get("cases", [])
    return [
        EvalCase(
            name=str(item["name"]),
            task=str(item["task"]),
            expected_contains=tuple(item.get("expected_contains", [])),
            forbidden_contains=tuple(item.get("forbidden_contains", [])),
            metadata=dict(item.get("metadata") or {}),
        )
        for item in items
    ]


def replay(path: str, outputs: str) -> dict:
    cases = load_cases(path)
    recorded = json.loads(Path(outputs).read_text(encoding="utf-8"))
    by_name = {str(item["name"]): str(item["output"]) for item in recorded}
    results = run_evals(cases, lambda case: by_name.get(case.name, ""))
    return {
        "passed": sum(result.passed for result in results),
        "total": len(results),
        "score": sum(result.score for result in results) / len(results) if results else 0,
        "results": [result.to_dict() for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases")
    parser.add_argument("outputs")
    args = parser.parse_args()
    summary = replay(args.cases, args.outputs)
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] == summary["total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
