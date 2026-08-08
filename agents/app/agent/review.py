"""Deterministic and risk-based model review before exposing a workspace diff."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.config import CHANGE_REVIEW_MODEL, CHANGE_REVIEW_MODEL_ENABLED
from ..llm.client import chat
from .completion import is_documentation_path
from .parser import ParserError, extract_json

_WEAK_REQUIREMENT_WORDS = {
    "agent",
    "autonomy",
    "implementation",
    "parallel",
    "scale",
    "structured",
    "with",
}
_HIGH_RISK_MARKERS = ("permission", "security", "auth", "memory", "migration", "api/")


def _diff_paths(diff: str) -> list[str]:
    return list(
        dict.fromkeys(
            match.group(1)
            for match in re.finditer(r"^diff --git a/(.+?) b/", diff, re.MULTILINE)
        )
    )


def _requirement_keywords(requirement: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[a-z][a-z0-9_]{3,}", requirement.casefold())
        if word not in _WEAK_REQUIREMENT_WORDS
    ][:16]


def _connected_paths(state, paths: list[str]) -> list[str]:
    ledger = getattr(state, "evidence_ledger", {}) or {}
    relevant = {str(path) for path in ledger.get("relevant_files", [])}
    tests = {str(path) for path in ledger.get("test_targets", [])}
    parents = {str(Path(path).parent) for path in relevant}
    return [
        path
        for path in paths
        if path not in relevant
        and path not in tests
        and str(Path(path).parent) not in parents
        and not Path(path).name.startswith("test_")
        and not is_documentation_path(path)
    ]


def validate_response_claims(state, answer: str, changed_paths: list[str]) -> list[str]:
    """Reject final claims contradicted by deterministic run evidence."""

    failures = []
    lowered = answer.casefold()
    if re.search(r"\b(?:tests?|suite|lint|build)\b.{0,40}\bpass", lowered):
        if not getattr(state, "successful_verification", False):
            failures.append(
                "response claims verification passed without a successful check"
            )
    mutation_paths = set(getattr(state, "successful_mutation_paths", set()) or set())
    if (
        changed_paths
        and mutation_paths
        and not mutation_paths.intersection(changed_paths)
    ):
        failures.append(
            "response mutation evidence does not match the actual diff paths"
        )
    if re.search(r"\b(?:implemented|completed|finished)\b", lowered):
        if getattr(state, "partial", False):
            failures.append("response claims completion for a partial run")
    return failures


def _needs_model_review(paths: list[str], diff: str) -> bool:
    lowered = " ".join(paths).casefold()
    return (
        len(paths) >= 3
        or any(marker in lowered for marker in _HIGH_RISK_MARKERS)
        or len(diff) > 20_000
    )


def _model_review(state, diff: str, deterministic: dict) -> dict:
    packet = {
        "requirement": getattr(state, "active_requirement", "")
        or getattr(state, "user_message", ""),
        "changed_paths": deterministic["changed_paths"],
        "verification": getattr(state, "successful_verification", False),
        "deterministic_findings": deterministic,
        "diff": diff[:12_000],
    }
    prompt = (
        "Review this code change independently. Return JSON only with keys "
        "decision ('accept' or 'reject'), implements_requirement (boolean), "
        "unrelated_changes (array), unsupported_claims (array), missing_tests "
        "(array), and risk ('low', 'medium', or 'high').\n\n"
        + json.dumps(packet, default=str)
    )
    try:
        data = extract_json(
            chat([{"role": "user", "content": prompt}], model=CHANGE_REVIEW_MODEL)
        )
    except (ParserError, ValueError, RuntimeError):
        return {"decision": "accept", "risk": "unknown", "review_unavailable": True}
    return (
        data
        if isinstance(data, dict)
        else {"decision": "accept", "review_unavailable": True}
    )


def review_change(state, diff: str, answer: str) -> dict:
    """Return an authoritative structured accept/reject decision for a diff."""

    paths = _diff_paths(diff)
    active = str(
        getattr(state, "active_requirement", "")
        or getattr(state, "active_roadmap_item", "")
        or getattr(state, "user_message", "")
    )
    keywords = _requirement_keywords(active)
    unrelated = _connected_paths(state, paths)
    response_failures = validate_response_claims(state, answer, paths)
    implementation_diff = "\n".join(
        line
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ).casefold()
    requirement_supported = not keywords or any(
        word in implementation_diff for word in keywords
    )
    reasons = []
    if not paths:
        reasons.append("diff contains no changed paths")
    if unrelated:
        reasons.append(
            "diff contains changes unrelated to analyzed source: "
            + ", ".join(unrelated)
        )
    if getattr(state, "active_roadmap_item", "") and not requirement_supported:
        reasons.append(
            "diff does not contain evidence of the active roadmap capability"
        )
    reasons.extend(response_failures)
    deterministic = {
        "decision": "reject" if reasons else "accept",
        "implements_requirement": requirement_supported and not unrelated,
        "changed_paths": paths,
        "unrelated_changes": unrelated,
        "unsupported_claims": response_failures,
        "missing_tests": (
            []
            if getattr(state, "successful_verification", False)
            else ["successful verification"]
        ),
        "risk": "high" if _needs_model_review(paths, diff) else "low",
        "reasons": reasons,
    }
    if (
        reasons
        or not CHANGE_REVIEW_MODEL_ENABLED
        or not _needs_model_review(paths, diff)
    ):
        return deterministic
    model_review = _model_review(state, diff, deterministic)
    if model_review.get("decision") == "reject":
        deterministic["decision"] = "reject"
        deterministic["reasons"].append("independent reviewer rejected the change")
    deterministic["model_review"] = model_review
    return deterministic
