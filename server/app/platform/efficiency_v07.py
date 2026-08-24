"""v0.7 measured efficiency/reliability composition for Server."""

from __future__ import annotations

from contextvars import ContextVar
import hashlib
import re
from typing import Any

from jarvis_core import RouteCandidate, route_roles

from .store import PlatformStore

_CATEGORY: ContextVar[str] = ContextVar("jarvis_route_category", default="code")
_INSTALLED = False


def task_category(task: str) -> str:
    text = task.casefold()
    if re.search(r"\b(bug|fix|regression|broken|error)\b", text):
        return "bugfix"
    if re.search(r"\b(security|auth|credential|permission|secret|vulnerability)\b", text):
        return "security"
    if re.search(r"\b(refactor|cleanup|simplify)\b", text):
        return "refactor"
    if re.search(r"\b(test|pytest|coverage|fixture)\b", text):
        return "tests"
    if re.search(r"\b(browser|frontend|ui|react|playwright)\b", text):
        return "frontend"
    if re.search(r"\b(architecture|design|migration|distributed)\b", text):
        return "architecture"
    if re.search(r"\b(research|web|latest|current)\b", text):
        return "research"
    return "code"


def failure_kind(text: str) -> str:
    lowered = text.casefold()
    classes = {
        "rate_limit": ("429", "rate limit"),
        "tool_protocol": ("tool schema", "tool protocol", "malformed tool"),
        "syntax": ("syntaxerror", "parse error"),
        "test": ("assertionerror", "test failed", "pytest"),
        "permission": ("permission denied", "not allowed"),
        "network": ("timeout", "connection refused", "network"),
        "wrong_symbol": ("unknown symbol", "wrong symbol"),
        "api_compat": ("breaking change", "incompatible"),
    }
    for name, needles in classes.items():
        if any(needle in lowered for needle in needles):
            return name
    return "unknown"


def retry_action(kind: str, attempts: int) -> dict[str, Any]:
    if attempts >= 3:
        return {"retry": False, "escalate": True, "action": "independent_route"}
    action = {
        "rate_limit": "fallback_or_backoff",
        "tool_protocol": "repair_message",
        "syntax": "local_repair",
        "test": "inspect_assertion",
        "permission": "request_approval",
        "network": "fallback_provider",
        "wrong_symbol": "refresh_graph_lsp",
        "api_compat": "compatibility_review",
    }.get(kind, "retry_once")
    return {
        "retry": kind not in {"permission", "api_compat"},
        "escalate": kind in {"permission", "api_compat"},
        "action": action,
    }


def _observed_failures(state) -> int:
    ledger = getattr(state, "execution_ledger", None)
    failures = 0
    if isinstance(ledger, dict):
        for rows in ledger.values():
            if isinstance(rows, list):
                failures += sum(
                    1
                    for row in rows
                    if isinstance(row, dict)
                    and (row.get("success") is False or ("exit_code" in row and row.get("exit_code") not in {0, None}))
                )
    return failures


def _risk_score(state) -> float:
    text = str(getattr(state, "user_message", "") or "").casefold()
    score = 0.1
    score += 0.18 * sum(
        token in text
        for token in ("security", "auth", "permission", "payment", "migration", "production", "delete")
    )
    score += min(_observed_failures(state), 3) * 0.12
    return min(1.0, score)


def _measured_candidates(default_model: str, category: str):
    from app.agent.quality import configured_candidates

    candidates = list(configured_candidates(default_model))
    try:
        rows = PlatformStore().route_scores(category)
    except Exception:
        rows = []
    measured = {
        str(row["route"]): row
        for row in rows
        if int(row.get("samples") or 0) >= 3
    }
    result = []
    for candidate in candidates:
        row = measured.get(candidate.profile) or measured.get(candidate.model)
        if row is None:
            result.append(candidate)
            continue
        success = max(0.0, min(1.0, float(row.get("success_rate") or 0)))
        incorrect = max(0.0, min(1.0, float(row.get("incorrect_rate") or 0)))
        latency = max(0.0, float(row.get("latency_ms") or 0))
        tokens = max(0.0, float(row.get("tokens") or 0))
        reliability = max(0.0, success - incorrect * 0.85)
        result.append(
            RouteCandidate(
                profile=candidate.profile,
                model=candidate.model,
                provider=candidate.provider,
                quality=reliability,
                tool_success=max(candidate.tool_success * 0.25, success),
                structured_success=candidate.structured_success,
                latency=1.0 / (1.0 + latency / 10000.0),
                cost=max(0.0, candidate.cost - min(tokens / 100000.0, 0.35)),
                roles=candidate.roles,
            )
        )
    return tuple(result)


def evidence_confidence(state) -> float:
    mutation = getattr(state, "mutation_events", []) or []
    verification = getattr(state, "verification_events", []) or []
    tests_ok = sum(
        1
        for row in verification
        if isinstance(row, dict) and row.get("kind") == "test" and row.get("exit_code") == 0
    )
    tests_bad = sum(
        1
        for row in verification
        if isinstance(row, dict) and row.get("kind") == "test" and row.get("exit_code") not in {0, None}
    )
    mutations_ok = sum(1 for row in mutation if isinstance(row, dict) and row.get("success") is True)
    test_score = tests_ok / max(1, tests_ok + tests_bad)
    mutation_score = 1.0 if not getattr(state, "allow_write", False) else min(1.0, mutations_ok)
    failures = _observed_failures(state)
    return max(0.0, min(1.0, 0.55 * test_score + 0.30 * mutation_score + 0.15 / (1.0 + failures)))


def record_failure(run_id: str, task: str, error: str, route: str | None = None) -> None:
    normalized = " ".join(error.casefold().split())[:1200]
    kind = failure_kind(error)
    fingerprint = hashlib.sha256(f"{kind}|{route or ''}|{normalized}".encode()).hexdigest()[:24]
    try:
        PlatformStore().record_failure_signature(
            run_id=run_id,
            fingerprint=fingerprint,
            kind=kind,
            category=task_category(task),
            route=route,
            detail=error[:4000],
            recovery=retry_action(kind, 1)["action"],
        )
    except Exception:
        return


def install_v07_efficiency() -> None:
    """Install task-aware measured routes and risk/failure-aware expert selection."""
    global _INSTALLED
    if _INSTALLED:
        return
    from app.agent import dispatch

    base_dispatch = dispatch.dispatch_experts
    base_select = dispatch._select_experts

    def expert_routes(roles: list[str], default_model: str):
        from app.agent.quality import ExpertRoute

        selected = route_roles(
            roles,
            _measured_candidates(default_model, _CATEGORY.get()),
            diverse=True,
        )
        return {
            item.role: ExpertRoute(
                role=item.role,
                profile=item.profile,
                model=item.model,
                provider=item.provider,
                score=item.score,
            )
            for item in selected
        }

    def select_experts(state):
        roles = list(base_select(state))
        failures = _observed_failures(state)
        risk = _risk_score(state)
        if failures >= 1 and "verification" not in roles:
            roles.append("verification")
        if (failures >= 2 or risk >= 0.65) and "risk" not in roles:
            roles.append("risk")
        if (failures >= 2 or risk >= 0.75) and "architecture" not in roles:
            roles.insert(0, "architecture")
        return roles[:4]

    def dispatch_experts(state, on_event=None):
        category = task_category(str(getattr(state, "user_message", "") or ""))
        token = _CATEGORY.set(category)
        try:
            return base_dispatch(state, on_event=on_event)
        finally:
            _CATEGORY.reset(token)

    dispatch.expert_routes = expert_routes
    dispatch._select_experts = select_experts
    dispatch.dispatch_experts = dispatch_experts
    _INSTALLED = True
