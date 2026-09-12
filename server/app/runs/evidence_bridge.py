"""Translate durable execution observations into Core evidence records."""
from __future__ import annotations
import hashlib
import json
from typing import Any
from jarvis_core.evidence import EvidenceLedger
from jarvis_core.execution_maturity import execution_evidence

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .store import RunStore
    original = RunStore.append_event

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]):
        enriched = dict(payload)
        if event_type in {"tool_result", "test_result", "command_result", "run_completed", "run_merged", "verification_passed"}:
            text = json.dumps(payload, sort_keys=True, default=str)
            ledger = EvidenceLedger()
            evidence = execution_evidence(
                ledger,
                claim=f"{event_type} observed for run {run_id}",
                kind=event_type,
                reference=f"run-event://{run_id}/{hashlib.sha256(text.encode()).hexdigest()}",
                output=text,
                verified=event_type not in {"test_result"} or bool(payload.get("passed", payload.get("success", False))),
            )
            enriched["core_evidence"] = evidence.to_dict()
        return original(self, run_id, event_type, enriched)

    RunStore.append_event = append_event
    _INSTALLED = True
