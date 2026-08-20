#!/usr/bin/env python3
"""Exercise a real Ollama model through the LiteLLM OpenAI tool protocol."""

import json
import os
import time
from urllib.request import Request, urlopen

url = os.getenv("INTEGRATION_BASE_URL", "http://127.0.0.1:14000/v1")
payload = {
    "model": "integration-tool-model",
    "messages": [
        {
            "role": "system",
            "content": "Call capability_probe exactly once with value ok.",
        },
        {"role": "user", "content": "Run the probe now."},
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "capability_probe",
                "description": "Verify native tool calling",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string", "enum": ["ok"]}},
                    "required": ["value"],
                },
            },
        }
    ],
    "tool_choice": "required",
    "max_tokens": 256,
}
last_error = None
for _ in range(6):
    try:
        request = Request(
            f"{url}/chat/completions",
            json.dumps(payload).encode(),
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer integration-key",
            },
        )
        with urlopen(request, timeout=180) as response:
            result = json.loads(response.read())
        calls = result["choices"][0]["message"].get("tool_calls") or []
        assert calls, result
        call = calls[0]["function"]
        assert call["name"] == "capability_probe", result
        assert json.loads(call["arguments"])["value"] == "ok", result
        print("real Ollama/LiteLLM tool-call smoke test passed")
        raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(5)
raise SystemExit(f"integration failed: {last_error}")
