#!/usr/bin/env python3
"""Emit a machine-readable cross-repository contract report."""
from __future__ import annotations
import json, os
from pathlib import Path

expected = {
    "jarvis_core": os.getenv("JARVIS_CORE_VERSION", "0.16.1"),
    "jarvis_cli": os.getenv("JARVIS_CLI_VERSION", "unknown"),
    "ai_stack": os.getenv("AI_STACK_VERSION", "unknown"),
    "inference": os.getenv("INFERENCE_VERSION", "unknown"),
}
report = {
    "schema": 1,
    "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    "expected_core": expected["jarvis_core"],
    "components": expected,
    "workspace": str(Path.cwd()),
    "status": "requires_runtime_validation",
}
print(json.dumps(report, indent=2, sort_keys=True))
