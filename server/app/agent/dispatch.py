"""Multi-expert dispatch for complex execution.

For complex or multi-workflow auto-routed requests the service layer marks the
state (``state.expert_dispatch``) and the executor calls ``dispatch_experts``
before its tool loop. Each expert is a bounded, tool-less completion that
returns structured JSON findings. The dispatcher validates and bounds every
finding so no expert output can exhaust the model context or inject prose, and
isolates per-expert failures so one bad response never fails the run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..core.config import (
    EXPERT_DISPATCH_MODEL,
    EXPERT_DISPATCH_TIMEOUT_SECONDS,
    EXPERT_MAX_COMPLETION_TOKENS,
    MAX_PARALLEL_EXPERTS,
)
from ..llm.client import chat
from .parser import extract_json
from .prompts import EXPERT_DISPATCH_PROMPT, EXPERT_ROLE_PROMPTS\nfrom .quality import analyze_state, expert_routes

logger = logging.getLogger(__name__)

# Ordered expert roster. Architecture is always dispatched; the remaining roles
# are added deterministically from the request's shape (write intent, external
# evidence, risk signals). Order here is the selection order up to the concurrency cap.
EXPERT_ROLES = ("architecture", "implementation", "verification", "risk")

_CONFIDENCE = {"high", "medium", "low"}
_MAX_FINDINGS_PER_EXPERT = 5
_MAX_EVIDENCE_PER_CLAIM = 4
_MAX_OPEN_QUESTIONS = 3
_MAX_RECOMMENDED_FOCUS = 4
_MAX_CLAIM_CHARS = 240
_MAX_ITEM_CHARS = 200
_MAX_EVIDENCE_PACKET_CHARS = 4_000


def _requested_workspace_change(state) -> bool:
    """Write-capable requests that actually ask for a change to files."""
    if not getattr(state, "allow_write", False):
        return False
    from .completion import requires_workspace_change

    return requires_workspace_change(getattr(state, "user_message", ""))


def _external_evidence_required(state) -> bool:
    return bool(getattr(state, "requires_external_evidence", False))


def _risk_signals(state) -> bool:
    message = (getattr(state, "user_message", "") or "").casefold()
    return any(
        token in message
        for token in (
            "migration",
            "refactor",
            "permission",
            "security",
            "auth",
            "breaking change",
            "risk",
        )
    )


def _select_experts(state) -> list[str]:
    """Deterministic subset of EXPERT_ROLES for the request's shape."""
    workspace_change = _requested_workspace_change(state)
    conditions = {
        "architecture": True,
        "implementation": workspace_change or _external_evidence_required(state),
        "verification": workspace_change,
        "risk": _risk_signals(state),
    }
    return [role for role in EXPERT_ROLES if conditions[role]][:MAX_PARALLEL_EXPERTS]


def _evidence_packet(state) -> str:
    ledger = getattr(state, "evidence_ledger", {}) or {}
    packet = {
        "requirements": ledger.get("requirements", [])[:6],
        "relevant_files": ledger.get("relevant_files", [])[:12],
        "owning_symbols": ledger.get("owning_symbols", [])[:12],
        "test_targets": ledger.get("test_targets", [])[:8],
        "dependencies": ledger.get("dependencies", {}) or {},
        "confirmed_existing": ledger.get("confirmed_existing", [])[:8],
        "confirmed_missing": ledger.get("confirmed_missing", [])[:8],
        "open_questions": ledger.get("open_questions", [])[:8],
    }
    text = json.dumps(packet, default=str)
    if len(text) > _MAX_EVIDENCE_PACKET_CHARS:
        text = text[:_MAX_EVIDENCE_PACKET_CHARS] + "\n...[evidence packet truncated]"
    return text


def _expert_prompt(state, role: str) -> str:
    brief = _bounded(getattr(state, "execution_brief", ""), 1_800)
    tasks = _bounded(getattr(state, "route_tasks", []), 1_500)
    return (
        f"{EXPERT_ROLE_PROMPTS[role]}\n\n"
        f"Task:\n{getattr(state, 'user_message', '')}\n\n"
        f"Execution brief:\n{brief}\n\n"
        f"Validated task graph:\n{tasks}\n\n"
        f"Verified workspace evidence packet:\n{_evidence_packet(state)}"
    )


def _bounded(value, limit: int) -> str:
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[context omitted]"


def _clamp_string(value: str, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit] if len(text) > limit else text


def _validate_finding(role: str, data: Any) -> dict[str, Any]:
    """Normalize arbitrary expert JSON into the bounded structured schema."""
    base = {
        "expert": role,
        "findings": [],
        "open_questions": [],
        "recommended_focus": [],
    }
    if not isinstance(data, dict):
        return base

    findings = []
    raw_findings = (
        data.get("findings") if isinstance(data.get("findings"), list) else []
    )
    for item in raw_findings[:_MAX_FINDINGS_PER_EXPERT]:
        if not isinstance(item, dict):
            continue
        claim = _clamp_string(item.get("claim", ""), _MAX_CLAIM_CHARS)
        if not claim:
            continue
        evidence_raw = item.get("evidence", [])
        evidence = [
            _clamp_string(path, _MAX_ITEM_CHARS)
            for path in (evidence_raw if isinstance(evidence_raw, list) else [])
            if isinstance(path, str)
        ][:_MAX_EVIDENCE_PER_CLAIM]
        confidence = str(item.get("confidence", "low")).lower()
        if confidence not in _CONFIDENCE:
            confidence = "low"
        findings.append(
            {"claim": claim, "evidence": evidence, "confidence": confidence}
        )

    for key, cap in (
        ("open_questions", _MAX_OPEN_QUESTIONS),
        ("recommended_focus", _MAX_RECOMMENDED_FOCUS),
    ):
        raw = data.get(key, [])
        if isinstance(raw, list):
            base[key] = [
                _clamp_string(value, _MAX_ITEM_CHARS)
                for value in raw
                if str(value or "").strip()
            ][:cap]
    base["findings"] = findings
    return base


def _unavailable(role: str, reason: str) -> dict[str, Any]:
    return {
        "expert": role,
        "findings": [
            {
                "claim": f"{role} expert unavailable: {reason}",
                "evidence": [],
                "confidence": "low",
            }
        ],
        "open_questions": [],
        "recommended_focus": [],
    }


def _run_expert(state, role: str, model: str | None = None) -> dict[str, Any]:
    response = chat(
        [
            {"role": "system", "content": EXPERT_DISPATCH_PROMPT},
            {"role": "user", "content": _expert_prompt(state, role)},
        ],
        model=model or EXPERT_DISPATCH_MODEL,
        max_tokens=EXPERT_MAX_COMPLETION_TOKENS,
        timeout_seconds=EXPERT_DISPATCH_TIMEOUT_SECONDS,
    )
    return _validate_finding(role, extract_json(response))


def dispatch_experts(
    state,
    on_event: Callable[[str, dict], None] | None = None,
) -> list[dict[str, Any]]:
    """Run the selected bounded expert analyses in parallel and return findings.

    Each expert is a single bounded completion. Failures are isolated: a broken
    role yields a structured "unavailable" finding instead of raising, so the
    executor always receives a mergeable ledger even when every expert fails.
    """
    analysis = analyze_state(state)
    roles = _select_experts(state) if analysis.needs_multi_agent else ["architecture"]
    if not roles:
        return []
    routes = expert_routes(roles, EXPERT_DISPATCH_MODEL)

    if on_event is not None:
        on_event(
            "expert_dispatch",
            {"roles": roles, "models": routes, "total": len(roles), "adaptive": True},
        )

    findings: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_EXPERTS, len(roles))) as pool:
        futures = {pool.submit(_run_expert, state, role, routes[role]): role for role in roles}
        for future in as_completed(futures):
            role = futures[future]
            try:
                findings.append(future.result())
            except Exception as exc:
                logger.exception("Expert '%s' raised during dispatch", role)
                findings.append(_unavailable(role, str(exc)[:120]))

    if on_event is not None:
        on_event(
            "expert_findings",
            {
                "count": len(findings),
                "total_findings": sum(
                    len(item.get("findings", [])) for item in findings
                ),
            },
        )
    return findings


def findings_context(findings: list[dict[str, Any]]) -> str:
    """Compact model-visible rendering of merged expert findings."""
    if not findings:
        return ""
    lines = []
    for item in findings:
        role = item.get("expert", "expert")
        claims = item.get("findings", [])
        if not claims:
            continue
        parts = []
        for claim in claims:
            evidence = claim.get("evidence", []) or []
            suffix = f" (evidence: {'; '.join(evidence)})" if evidence else ""
            parts.append(
                f"[{claim.get('confidence', 'low')}] {claim.get('claim', '')}{suffix}"
            )
        lines.append(f"{role}: " + "; ".join(parts))
    return "\n".join(lines)
