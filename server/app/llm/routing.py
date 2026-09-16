"""Capability-aware and cost-aware model routing shared with standalone Jarvis."""

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
from jarvis_core.cost_router import RouteModel, RouteTier, RoutingSignals, choose_tier, select_model

from .empirical import select_empirical_route


def _csv_env(name: str, default: str = "") -> set[str]:
    return {item.strip() for item in os.getenv(name, default).split(",") if item.strip()}


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
    complexity: float = 0.0,
    risk: float = 0.0,
    uncertainty: float = 0.0,
    attempts: int = 0,
    tool_failures: int = 0,
    security_sensitive: bool = False,
) -> str:
    """Route local-first, escalating only when deterministic signals justify it.

    Tier membership is configured with JARVIS_LOCAL_MODELS, JARVIS_CHEAP_MODELS,
    and JARVIS_FRONTIER_MODELS. Names are model ids already present in
    ``available``. Existing empirical routing remains the tie-breaker inside a
    selected tier.
    """
    registry = model_registry(available)
    if preferred not in {None, "auto"}:
        return registry.select(preferred=preferred, required=required).model

    candidates = [
        item for item in registry.list()
        if item.capabilities.supports(required)
        and _health.setdefault(item.model, ProviderHealth(item.model)).state.value != "open"
    ]
    if not candidates:
        raise LookupError("no healthy model satisfies the required capabilities")

    signals = RoutingSignals(
        complexity=complexity,
        risk=risk,
        uncertainty=uncertainty,
        attempts=attempts,
        tool_failures=tool_failures,
        security_sensitive=security_sensitive,
    )
    tier = choose_tier(signals).tier

    local = _csv_env("JARVIS_LOCAL_MODELS", "quick,coder,qwen3-8b,qwen3-14b,reasoning")
    cheap = _csv_env("JARVIS_CHEAP_MODELS")
    frontier = _csv_env("JARVIS_FRONTIER_MODELS")
    tier_names = {
        RouteTier.LOCAL: local,
        RouteTier.CHEAP: cheap,
        RouteTier.FRONTIER: frontier,
    }

    # If the requested tier has no configured model, walk upward rather than
    # silently selecting a more expensive model than the deployment allows.
    ordered = [tier, RouteTier.CHEAP, RouteTier.FRONTIER]
    if tier is RouteTier.CHEAP:
        ordered = [RouteTier.CHEAP, RouteTier.FRONTIER]
    elif tier is RouteTier.FRONTIER:
        ordered = [RouteTier.FRONTIER]

    available_names = {item.model for item in candidates} | {item.name for item in candidates}
    for candidate_tier in ordered:
        names = tier_names[candidate_tier] & available_names
        if not names:
            continue
        selected = select_model(
            tuple(RouteModel(name=item.model, tier=candidate_tier, priority=item.priority) for item in candidates if item.model in names),
            candidate_tier,
        )
        return selected.name

    # Backward-compatible fallback for deployments that have not declared tiers.
    candidate_models = [item.model for item in candidates]
    fallback = max(candidates, key=lambda item: (_health[item.model].score, item.priority, item.name)).model
    benchmarks = _benchmark_registry()
    if benchmarks.observations:
        fallback = benchmarks.select(candidates, task).model
    return select_empirical_route(candidate_models, task, fallback=fallback)
