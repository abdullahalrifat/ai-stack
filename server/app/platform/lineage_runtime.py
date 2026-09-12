"""Runtime binding for parent/child expert proof lineage."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_INSTALLED = False


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def bind_findings(findings: list[dict[str, Any]], parent_task_id: str, on_event=None) -> list[dict[str, Any]]:
    root = parent_task_id
    bound: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        role = str(finding.get("expert") or "expert")
        child_task_id = f"{parent_task_id}:expert:{role}:{index}"
        result_digest = _sha(finding)
        evidence_digests = tuple(_sha(item) for item in finding.get("findings", []) if isinstance(item, dict))
        proof_payload = {
            "task_id": child_task_id,
            "parent_task_id": parent_task_id,
            "root_task_id": root,
            "depth": 1,
            "role": role,
            "result_digest": result_digest,
            "evidence_digests": list(evidence_digests),
        }
        proof_payload["proof_digest"] = _sha(proof_payload)
        item = dict(finding)
        item["lineage"] = proof_payload
        bound.append(item)
    if on_event is not None and bound:
        on_event("expert_lineage_bound", {"parent_task_id": parent_task_id, "children": [item["lineage"] for item in bound]})
    return bound


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from app.agent import dispatch
    original = dispatch.dispatch_experts

    def wrapped(state, on_event=None):
        findings = original(state, on_event=on_event)
        parent = str(getattr(state, "run_id", None) or getattr(state, "task_id", None) or "run:unknown")
        return bind_findings(findings, parent, on_event=on_event)

    dispatch.dispatch_experts = wrapped
    _INSTALLED = True
