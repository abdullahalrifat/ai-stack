#!/usr/bin/env python3
"""Secret-gated live tool-call probes for remote providers."""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen


TOOL = {
    "name": "capability_probe",
    "description": "Verify native tool calling",
    "parameters": {
        "type": "object",
        "properties": {"value": {"type": "string", "enum": ["ok"]}},
        "required": ["value"],
    },
}


def post(url: str, payload: dict, headers: dict[str, str]) -> dict:
    with urlopen(
        Request(
            url,
            json.dumps(payload).encode(),
            {"Content-Type": "application/json", **headers},
        ),
        timeout=120,
    ) as response:
        return json.loads(response.read())


def openai_probe() -> None:
    base = os.getenv("REMOTE_OPENAI_BASE_URL", "").rstrip("/")
    model = os.getenv("REMOTE_OPENAI_MODEL", "")
    key = os.getenv("REMOTE_OPENAI_API_KEY", "")
    if not (base and model and key):
        print("OpenAI-compatible smoke test skipped: secrets are not configured")
        return
    result = post(
        f"{base}/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": "Call capability_probe with ok"}],
            "tools": [{"type": "function", "function": TOOL}],
            "tool_choice": "required",
            "max_tokens": 256,
        },
        {"Authorization": f"Bearer {key}"},
    )
    calls = result["choices"][0]["message"].get("tool_calls") or []
    assert calls and calls[0]["function"]["name"] == "capability_probe"
    assert json.loads(calls[0]["function"]["arguments"])["value"] == "ok"


def anthropic_probe() -> None:
    model = os.getenv("ANTHROPIC_MODEL", "")
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not (model and key):
        print("Anthropic smoke test skipped: secrets are not configured")
        return
    result = post(
        "https://api.anthropic.com/v1/messages",
        {
            "model": model,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "Call capability_probe with ok"}],
            "tools": [
                {
                    "name": TOOL["name"],
                    "description": TOOL["description"],
                    "input_schema": TOOL["parameters"],
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL["name"]},
        },
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    calls = [item for item in result["content"] if item["type"] == "tool_use"]
    assert calls and calls[0]["name"] == "capability_probe"
    assert calls[0]["input"]["value"] == "ok"



def local_inference_contract_probe() -> None:
    base = os.getenv("INFERENCE_BASE_URL", "").rstrip("/")
    key = os.getenv("INFERENCE_API_KEY", "")
    if not (base and key):
        print("Inference contract smoke test skipped: secrets are not configured")
        return
    headers = {"Authorization": f"Bearer {key}"}
    with urlopen(Request(f"{base}/capabilities", headers=headers), timeout=10) as response:
        capabilities = json.loads(response.read())
    assert capabilities["protocol"]["current"] == 1
    assert {"chat", "streaming", "embeddings"}.issubset(capabilities["features"])
    model_ids = {item["id"] for item in capabilities["models"]}
    assert os.getenv("DEFAULT_MODEL", "qwen3:1.7b") in model_ids
    embedding_models = {
        item["id"] for item in capabilities["models"] if "embeddings" in item["capabilities"]
    }
    embedding_model = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    assert embedding_model in embedding_models
    result = post(
        f"{base}/embeddings",
        {"model": embedding_model, "input": "cross-repo contract smoke"},
        headers,
    )
    assert result["data"] and result["data"][0]["embedding"]


def ai_stack_inference_contract_probe() -> None:
    base = os.getenv("AI_STACK_BASE_URL", "").rstrip("/")
    key = os.getenv("AGENT_API_KEY", "")
    if not (base and key):
        print("AI Stack integration smoke test skipped: secrets are not configured")
        return
    model = os.getenv("DEFAULT_MODEL", "qwen3:1.7b")
    with urlopen(
        Request(
            f"{base}/diagnostics/inference?model={model}",
            headers={"Authorization": f"Bearer {key}"},
        ),
        timeout=120,
    ) as response:
        result = json.loads(response.read())
    assert result["embedding"]["dimensions"] > 0
    assert result["generation"]["answer"]



local_inference_contract_probe()
ai_stack_inference_contract_probe()
openai_probe()
anthropic_probe()
print("configured remote-provider probes passed")
