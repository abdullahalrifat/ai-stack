"""Adaptive quality, heterogeneous routing, evidence, and isolated worktree helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jarvis_core import (
    ClaimProof,
    CompletionRequirement,
    EvidenceGate,
    ProofKind,
    RouteCandidate,
    Scope,
    TaskAnalysis,
    route_roles,
    stable_cache_key,
)


def _number(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ExpertRoute:
    role: str
    profile: str
    model: str
    provider: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def configured_candidates(default_model: str) -> tuple[RouteCandidate, ...]:
    """Load benchmark-derived route metrics from trusted configuration."""
    try:
        raw = json.loads(os.getenv("QUALITY_MODEL_ROUTES", "[]"))
    except json.JSONDecodeError:
        raw = []
    candidates: list[RouteCandidate] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not item.get("model"):
            continue
        candidates.append(
            RouteCandidate(
                profile=str(item.get("profile") or item["model"]),
                model=str(item["model"]),
                provider=str(item.get("provider", "openai")),
                quality=_number(item.get("quality"), 0.5),
                tool_success=_number(item.get("tool_success"), 0.5),
                structured_success=_number(item.get("structured_success"), 0.5),
                latency=_number(item.get("latency"), 0.5),
                cost=_number(item.get("cost"), 0.5),
                roles=tuple(str(role) for role in item.get("roles", ())),
            )
        )
    if not candidates:
        candidates.append(
            RouteCandidate("default", default_model, "openai", 0.6, 0.6, 0.6, 0.5, 0.2)
        )
    return tuple(candidates)


def expert_routes(roles: list[str], default_model: str) -> dict[str, ExpertRoute]:
    """Return complete role routes instead of collapsing routing to model names."""
    selected = route_roles(roles, configured_candidates(default_model), diverse=True)
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


def analyze_state(state) -> TaskAnalysis:
    message = str(getattr(state, "user_message", "") or "")
    risk = (
        0.75
        if re.search(
            r"\b(auth|credential|migration|payment|permission|security)\b",
            message,
            re.I,
        )
        else 0.2
    )
    complexity = (
        0.75
        if re.search(
            r"\b(across|architecture|entire|multi[- ]|refactor|repository)\b",
            message,
            re.I,
        )
        else 0.3
    )
    write = bool(getattr(state, "allow_write", False))
    roles = (
        ("architecture", "implementation", "verification")
        if write
        else ("architecture",)
    )
    return TaskAnalysis(
        complexity,
        risk,
        Scope.MULTI_MODULE if complexity > 0.5 else Scope.SINGLE_FILE,
        write,
        bool(getattr(state, "requires_external_evidence", False)),
        ("tests",) if write else (),
        roles,
    )


def _ledger_entries(state, name: str) -> list[dict[str, Any]]:
    value = getattr(state, name, None)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    ledger = getattr(state, "execution_ledger", None)
    if isinstance(ledger, dict) and isinstance(ledger.get(name), list):
        return [item for item in ledger[name] if isinstance(item, dict)]
    return []


def _digest_entry(entry: dict[str, Any]) -> str:
    payload = json.dumps(entry, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _has_explicit_execution_ledger(state) -> bool:
    """True for real v0.4 runtime states that must never trust success booleans."""
    return (
        hasattr(state, "mutation_events")
        or hasattr(state, "verification_events")
        or isinstance(getattr(state, "execution_ledger", None), dict)
    )


def evidence_audit(state):
    """Audit completion against execution-derived proof.

    Real v0.4 AgentState objects expose explicit mutation/verification ledgers and
    are strictly evidence-gated. Lightweight legacy state doubles without those
    fields retain compatibility with older unit contracts, but production state
    can never fall back to model- or flag-derived proof.
    """
    analysis = analyze_state(state)
    requirements: list[CompletionRequirement] = []
    proofs: list[ClaimProof] = []
    mutation_entries = _ledger_entries(state, "mutation_events")
    verification_entries = _ledger_entries(state, "verification_events")
    strict = _has_explicit_execution_ledger(state)

    if analysis.requires_write:
        requirements.append(
            CompletionRequirement("workspace mutation", (ProofKind.MUTATION,))
        )
        for entry in mutation_entries:
            if entry.get("success") is True and entry.get("path"):
                proofs.append(
                    ClaimProof(
                        "workspace mutation",
                        ProofKind.MUTATION,
                        f"mutation:{_digest_entry(entry)}",
                        digest=str(entry.get("after_sha256") or "") or None,
                    )
                )
        if not strict and getattr(state, "successful_mutation", False):
            proofs.append(
                ClaimProof(
                    "workspace mutation",
                    ProofKind.MUTATION,
                    "legacy-test-state:mutation",
                )
            )

        requirements.append(
            CompletionRequirement("verification", (ProofKind.TEST, ProofKind.COMMAND))
        )
        for entry in verification_entries:
            if entry.get("exit_code") == 0:
                proofs.append(
                    ClaimProof(
                        "verification",
                        ProofKind.TEST if entry.get("kind") == "test" else ProofKind.COMMAND,
                        f"execution:{_digest_entry(entry)}",
                        digest=str(entry.get("stdout_sha256") or "") or None,
                    )
                )
        if not strict and getattr(state, "successful_verification", False):
            proofs.append(
                ClaimProof(
                    "verification",
                    ProofKind.TEST,
                    "legacy-test-state:verification",
                )
            )

    return EvidenceGate().audit(requirements, proofs)


class QualityRecorder:
    def __init__(self, path: Path) -> None:
        self.path, self._lock = path, threading.Lock()

    def record(self, payload: dict[str, Any]) -> None:
        event = {"timestamp": time.time(), **payload}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True, default=str) + "\n")


class ResultCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, namespace: str, request: Any) -> Path:
        return self.root / stable_cache_key(namespace, request)


class WorktreePool:
    """One unique branch and worktree per implementation owner."""

    def __init__(self, repository: Path, root: Path) -> None:
        self.repository, self.root = repository.resolve(), root.resolve()

    def create(self, task_id: str, base: str = "HEAD") -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", task_id).strip("-")
        if not safe:
            raise ValueError("invalid task id")
        suffix = hashlib.sha256(f"{safe}:{time.time_ns()}".encode()).hexdigest()[:8]
        name = f"{safe}-{suffix}"
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "worktree",
                "add",
                "-b",
                f"agent/{name}",
                str(target),
                base,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return target
