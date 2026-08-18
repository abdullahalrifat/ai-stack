"""Render LiteLLM configuration with optional remote providers.

The checked-in config remains local-only. Remote providers are appended only
when their complete environment configuration is present, so existing homelab
deployments continue to start without cloud credentials.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


MARKER = "###########################################################\n# General Settings"


def _yaml_string(value: str) -> str:
    return json.dumps(value)


def remote_model_entries(env: dict[str, str]) -> str:
    entries: list[str] = []

    anthropic_model = env.get("ANTHROPIC_MODEL", "").strip()
    anthropic_key = env.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_model or anthropic_key:
        if not (anthropic_model and anthropic_key):
            raise ValueError(
                "ANTHROPIC_MODEL and ANTHROPIC_API_KEY must be configured together"
            )
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

    hf_model = env.get("HF_MODEL", "").strip()
    hf_base = env.get("HF_INFERENCE_BASE_URL", "").strip().rstrip("/")
    hf_key = env.get("HF_API_KEY", "").strip()
    if hf_model or hf_base or hf_key:
        if not (hf_model and hf_base and hf_key):
            raise ValueError(
                "HF_MODEL, HF_INFERENCE_BASE_URL, and HF_API_KEY "
                "must be configured together"
            )
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
            % (_yaml_string(f"openai/{hf_model}"), _yaml_string(hf_base))
        )

    return "".join(entries)


def render_config(source: str, env: dict[str, str]) -> str:
    entries = remote_model_entries(env)
    if not entries:
        return source
    if MARKER not in source:
        raise ValueError("LiteLLM configuration is missing the insertion marker")
    return source.replace(MARKER, f"{entries}\n{MARKER}", 1)


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
