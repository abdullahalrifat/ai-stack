"""Adaptive quality, heterogeneous routing, evidence, and isolated worktree helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jarvis_core import (
    ClaimProof, CompletionRequirement, EvidenceGate, ProofKind,
    QualityMetrics, RouteCandidate, Scope, TaskAnalysis, adaptive_plan,
    route_roles, stable_cache_key,
)


def _number(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def configured_candidates(default_model: str) -> tuple[RouteCandidate, ...]:
    """Load bounded route metrics without treating model-provided scores as truth."""
    try:
        raw = json.loads(os.getenv("QUALITY_MODEL_ROUTES", "[]"))
    except json.JSONDecodeError:
        raw = []
    candidates = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not item.get("model"):
            continue
        candidates.append(RouteCandidate(
            profile=str(item.get("profile") or item["model"]),
            model=str(item["model"]),
            provider=str(item.get("provider", "openai")),
            quality=_number(item.get("quality"), .5),
            tool_success=_number(item.get("tool_success"), .5),
            structured_success=_number(item.get("structured_success"), .5),
            latency=_number(item.get("latency"), .5),
            cost=_number(item.get("cost"), .5),
            roles=tuple(str(role) for role in item.get("roles", ())),
        ))
    if not candidates:
        candidates.append(RouteCandidate(
            "default", default_model, "openai", .6, .6, .6, .5, .2,
        ))
    return tuple(candidates)


def expert_routes(roles: list[str], default_model: str) -> dict[str, str]:
    selected = route_roles(roles, configured_candidates(default_model), diverse=True)
    return {item.role: item.model for item in selected}


def analyze_state(state) -> TaskAnalysis:
    message = str(getattr(state, "user_message", "") or "")
    risk = .75 if re.search(r"\b(auth|credential|migration|payment|permission|security)\b", message, re.I) else .2
    complexity = .75 if re.search(r"\b(across|architecture|entire|multi[- ]|refactor|repository)\b", message, re.I) else .3
    write = bool(getattr(state, "allow_write", False))
    roles = ("architecture", "implementation", "verification") if write else ("architecture",)
    return TaskAnalysis(complexity, risk, Scope.MULTI_MODULE if complexity > .5 else Scope.SINGLE_FILE, write, bool(getattr(state, "requires_external_evidence", False)), ("tests",) if write else (), roles)


def evidence_audit(state):
    requirements = []
    proofs = []
    if getattr(state, "successful_mutation", False):
        proofs.append(ClaimProof("workspace mutation", ProofKind.MUTATION, "tool-ledger"))
    elif analyze_state(state).requires_write:
        requirements.append(CompletionRequirement("workspace mutation", (ProofKind.MUTATION,)))
    if analyze_state(state).requires_write:
        requirements.append(CompletionRequirement("verification", (ProofKind.TEST, ProofKind.COMMAND)))
        if getattr(state, "successful_verification", False):
            proofs.append(ClaimProof("verification", ProofKind.TEST, "verification-ledger"))
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
    """One branch and worktree per implementation owner."""

    def __init__(self, repository: Path, root: Path) -> None:
        self.repository, self.root = repository.resolve(), root.resolve()

    def create(self, task_id: str, base: str = "HEAD") -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", task_id).strip("-")
        if not safe:
            raise ValueError("invalid task id")
        target = self.root / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(self.repository), "worktree", "add", "-b", f"agent/{safe}", str(target), base], check=True, capture_output=True, text=True)
        return target
