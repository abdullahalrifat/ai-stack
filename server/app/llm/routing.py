"""Capability-aware model routing shared with standalone Jarvis."""

from __future__ import annotations

import json
import os

from jarvis_core import CapabilityRegistry, ModelCapabilities, ModelProfile


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


def route_model(
    available: list[str],
    *,
    preferred: str | None = "auto",
    required: tuple[str, ...] = ("tool_calling",),
) -> str:
    profile = model_registry(available).select(
        preferred=preferred,
        required=required,
    )
    return profile.model
