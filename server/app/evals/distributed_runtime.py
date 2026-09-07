"""Deterministic distributed execution evaluation harness.

This harness models the Server -> worker -> completion lifecycle around the
real HTTP/store contracts. It deliberately does not call a language model;
quality benchmarks can inject a real task executor while lifecycle correctness
stays independently measurable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping


@dataclass(frozen=True)
class DistributedCase:
    id: str
    task: str
    expected_state: str = "completed"
    category: str = "code"


@dataclass
class DistributedResult:
    case_id: str
    success: bool
    false_completion: bool
    latency_seconds: float
    states: list[str] = field(default_factory=list)
    error: str | None = None


def run_case(
    case: DistributedCase,
    executor: Callable[[DistributedCase], Mapping[str, object]],
) -> DistributedResult:
    started = time.monotonic()
    try:
        outcome = executor(case)
        states = [str(value) for value in outcome.get("states", [])]
        claimed = bool(outcome.get("completed", False))
        success = claimed and str(outcome.get("state", "")) == case.expected_state
        false_completion = claimed and not success
        return DistributedResult(
            case_id=case.id,
            success=success,
            false_completion=false_completion,
            latency_seconds=time.monotonic() - started,
            states=states,
            error=None if success else str(outcome.get("error", "invalid completion")),
        )
    except Exception as exc:  # noqa: BLE001 - harness records executor faults.
        return DistributedResult(
            case_id=case.id,
            success=False,
            false_completion=False,
            latency_seconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


def aggregate(results: list[DistributedResult]) -> dict[str, float | int]:
    total = len(results)
    successes = sum(item.success for item in results)
    false = sum(item.false_completion for item in results)
    latencies = sorted(item.latency_seconds for item in results)
    if not latencies:
        median = 0.0
    else:
        middle = len(latencies) // 2
        median = (
            latencies[middle]
            if len(latencies) % 2
            else (latencies[middle - 1] + latencies[middle]) / 2
        )
    return {
        "total": total,
        "successes": successes,
        "task_success_rate": successes / total if total else 0.0,
        "false_completions": false,
        "incorrect_completion_rate": false / total if total else 0.0,
        "median_latency_seconds": median,
    }
