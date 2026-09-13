"""Capability-aware model routing shared with standalone Jarvis."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock

from jarvis_core import (
    BenchmarkObservation,
    BenchmarkRegistry,
    CapabilityRegistry,
    ModelCapabilities,
    ModelProfile,
    ProviderHealth,
)

from .empirical import select_empirical_route


def model_registry(available: list[str]) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    configured = os.getenv("JARVIS_MODEL_PROFILES_JSON", "")
    if configured:
        payload = json.loads(configured)
        for name, item in payload.items():
            model = str(item["model"])
            if model not in available:
                continue
            caps = item.get("capabilities") or {}
            registry.add(
                ModelProfile(
                    name=str(name),
                    provider=str(item.get("provider", "openai")),
                    model=model,
                    base_url=str(item.get("base_url", "")),
                    priority=int(item.get("priority", 0)),
                    enabled=bool(item.get("enabled", True)),
                    capabilities=ModelCapabilities(
                        tool_calling=bool(caps.get("tool_calling", True)),
                        structured_output=bool(caps.get("structured_output", False)),
                        vision=bool(caps.get("vision", False)),
                        context_tokens=int(caps.get("context_tokens", 0)),
                        max_output_tokens=int(caps.get("max_output_tokens", 0)),
                        first_token_ms=caps.get("first_token_ms"),
                        tokens_per_second=caps.get("tokens_per_second"),
                        tool_success_rate=caps.get("tool_success_rate"),
                    ),
                )
            )
    for model in available:
        if not any(profile.model == model for profile in registry.list()):
            registry.add(
                ModelProfile(
                    name=model,
                    provider="openai",
                    model=model,
                    base_url="",
                    capabilities=ModelCapabilities(tool_calling=True),
                )
            )
    return registry


_lock = Lock()
_health: dict[str, ProviderHealth] = {}


def _benchmark_registry() -> BenchmarkRegistry:
    registry = BenchmarkRegistry()
    path = Path(os.getenv("JARVIS_MODEL_BENCHMARKS", "/tmp/jarvis-model-benchmarks.jsonl"))
    if not path.exists():
        return registry
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            registry.record(BenchmarkObservation(**json.loads(line)))
    return registry


def record_model_observation(observation: BenchmarkObservation) -> None:
    path = Path(os.getenv("JARVIS_MODEL_BENCHMARKS", "/tmp/jarvis-model-benchmarks.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as output:
        from dataclasses import asdict

        output.write(json.dumps(asdict(observation)) + "\n")


def record_model_health(model: str, *, success: bool, latency_ms: float = 0) -> None:
    health = _health.setdefault(model, ProviderHealth(model))
    if success:
        health.record_success(latency_ms)
    else:
        health.record_failure()


def route_model(
    available: list[str],
    *,
    preferred: str | None = "auto",
    required: tuple[str, ...] = ("tool_calling",),
    task: str = "general",
) -> str:
    registry = model_registry(available)
    if preferred not in {None, "auto"}:
        return registry.select(preferred=preferred, required=required).model
    candidates = [
        item
        for item in registry.list()
        if item.capabilities.supports(required)
        and _health.setdefault(item.model, ProviderHealth(item.model)).state.value != "open"
    ]
    if not candidates:
        raise LookupError("no healthy model satisfies the required capabilities")

    candidate_models = [item.model for item in candidates]
    fallback = max(
        candidates,
        key=lambda item: (_health[item.model].score, item.priority, item.name),
    ).model

    benchmarks = _benchmark_registry()
    if benchmarks.observations:
        fallback = benchmarks.select(candidates, task).model

    return select_empirical_route(candidate_models, task, fallback=fallback)
