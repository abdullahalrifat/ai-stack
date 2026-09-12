"""Security-boundary helpers for untrusted cloud task content.

The Server treats task text, repository files, evidence, MCP responses and model
output as data. These helpers provide small, deterministic checks that can be
used by the API/evaluation layers without attempting to execute or trust the
content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*)(?!bearer\b)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)(\bauthorization\s*:\s*bearer\s+)[^\s,;]+")

INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all prior instructions",
    "system prompt",
    "developer message",
    "reveal your secrets",
    "print environment variables",
    "send the token",
    "disable safety",
)


@dataclass(frozen=True)
class SecurityScan:
    injection_markers: tuple[str, ...]
    secret_canaries: tuple[str, ...]

    @property
    def unsafe(self) -> bool:
        return bool(self.injection_markers or self.secret_canaries)


def scan_untrusted_text(text: str, secret_canaries: Iterable[str] = ()) -> SecurityScan:
    """Detect known prompt-injection phrases and exact canary values."""

    haystack = str(text)
    folded = haystack.casefold()
    injections = tuple(marker for marker in INJECTION_MARKERS if marker in folded)
    canaries = tuple(
        canary for canary in secret_canaries if canary and canary in haystack
    )
    return SecurityScan(injection_markers=injections, secret_canaries=canaries)


def redact_sensitive_text(text: str) -> str:
    """Redact common credential assignments while preserving the key name."""

    text = _BEARER.sub(r"\1[REDACTED]", text)
    return _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", text)


def scrub_payload(value: Any, secret_canaries: Iterable[str] = ()) -> Any:
    """Recursively scrub strings in API/proof/telemetry-shaped payloads."""

    canaries = tuple(secret_canaries)
    if isinstance(value, str):
        result = redact_sensitive_text(value)
        for canary in canaries:
            if canary:
                result = result.replace(canary, "[REDACTED]")
        return result
    if isinstance(value, dict):
        return {str(key): scrub_payload(item, canaries) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_payload(item, canaries) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_payload(item, canaries) for item in value)
    return value


def assert_no_canary(value: Any, secret_canaries: Iterable[str]) -> None:
    """Raise if a secret canary survives in a structured value."""

    canaries = tuple(secret_canaries)
    if not canaries:
        return
    text = str(value)
    leaked = [canary for canary in canaries if canary and canary in text]
    if leaked:
        raise AssertionError(f"secret canary leaked: {leaked[0]}")
