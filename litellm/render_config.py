"""Render LiteLLM configuration with optional hybrid remote providers.

The checked-in config remains local-first and offline-safe. When
INFERENCE_MODE=hybrid and Hugging Face is configured, selected capability
aliases are promoted to HF-backed primary deployments and automatically fall
back to their local Ollama aliases.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

MARKER = "###########################################################\n# General Settings"


def _yaml_string(value: str) -> str:
    return json.dumps(value)


def _hybrid_enabled(env: dict[str, str]) -> bool:
    return env.get("INFERENCE_MODE", "local").strip().lower() == "hybrid"


def _remote_enabled(env: dict[str, str]) -> bool:
    return env.get("INFERENCE_MODE", "local").strip().lower() == "hybrid"


def _hf_settings(env: dict[str, str]) -> dict[str, str]:
    base = env.get("HF_INFERENCE_BASE_URL", "").strip().rstrip("/")
    key = env.get("HF_API_KEY", "").strip()
    if bool(base) != bool(key):
        raise ValueError("HF_INFERENCE_BASE_URL and HF_API_KEY must be configured together")
    return {"base": base, "key": key}


def _hf_model(env: dict[str, str], role: str) -> str:
    return env.get(f"HF_{role.upper()}_MODEL", "").strip() or env.get("HF_MODEL", "").strip()


def _hybrid_entries(env: dict[str, str]) -> tuple[str, list[str]]:
    if not _hybrid_enabled(env):
        return "", []
    settings = _hf_settings(env)
    if not settings["base"] and not settings["key"]:
        return "", []

    roles = {"coder": "coder-local", "reasoning": "reasoning-local", "vision": "vision-local"}
    entries: list[str] = []
    fallbacks: list[str] = []
    for role, local_alias in roles.items():
        model = _hf_model(env, role)
        if not model:
            continue
        entries.append(
            f"""
  - model_name: {role}
    litellm_params:
      model: {_yaml_string(f"openai/{model}")}
      api_base: {_yaml_string(settings["base"])}
      api_key: os.environ/HF_API_KEY
    model_info:
      supports_function_calling: true
"""
        )
        fallbacks.append(f"    - {role}: [{local_alias}]\n")
    return "".join(entries), fallbacks


def remote_model_entries(env: dict[str, str]) -> str:
    entries: list[str] = []
    if not _remote_enabled(env):
        return ""

    anthropic_model = env.get("ANTHROPIC_MODEL", "").strip()
    anthropic_key = env.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_model or anthropic_key:
        if not (anthropic_model and anthropic_key):
            raise ValueError("ANTHROPIC_MODEL and ANTHROPIC_API_KEY must be configured together")
        entries.append(
            """
  - model_name: remote-claude
    litellm_params:
      model: %s
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info:
      supports_function_calling: true
"""
            % _yaml_string(f"anthropic/{anthropic_model}")
        )

    settings = _hf_settings(env)
    hf_model = env.get("HF_MODEL", "").strip()
    if hf_model or settings["base"] or settings["key"]:
        if not (hf_model and settings["base"] and settings["key"]):
            raise ValueError("HF_MODEL, HF_INFERENCE_BASE_URL, and HF_API_KEY must be configured together")
        entries.append(
            """
  - model_name: remote-hf
    litellm_params:
      model: %s
      api_base: %s
      api_key: os.environ/HF_API_KEY
    model_info:
      supports_function_calling: true
"""
            % (_yaml_string(f"openai/{hf_model}"), _yaml_string(settings["base"]))
        )

    hybrid, _ = _hybrid_entries(env)
    entries.append(hybrid)
    return "".join(entries)


def _local_alias_rewrites(source: str, env: dict[str, str]) -> str:
    hybrid, _ = _hybrid_entries(env)
    if not hybrid:
        return source
    for public, local in (("coder", "coder-local"), ("reasoning", "reasoning-local"), ("vision", "vision-local")):
        source = source.replace(f"  - model_name: {public}\n", f"  - model_name: {local}\n", 1)
    return source


def render_config(source: str, env: dict[str, str]) -> str:
    source = _local_alias_rewrites(source, env)
    entries = remote_model_entries(env)
    if not entries:
        return source
    if MARKER not in source:
        raise ValueError("LiteLLM configuration is missing the insertion marker")

    _, fallback_lines = _hybrid_entries(env)
    rendered = source.replace(MARKER, f"{entries}\n{MARKER}", 1)
    if fallback_lines:
        fallback_yaml = "router_settings:\n  routing_strategy: simple-shuffle\n  fallbacks:\n" + "".join(fallback_lines)
        rendered = rendered.replace(
            "router_settings:\n  routing_strategy: simple-shuffle\n",
            fallback_yaml,
            1,
        )
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input).read_text(encoding="utf-8")
    rendered = render_config(source, dict(os.environ))
    Path(args.output).write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
