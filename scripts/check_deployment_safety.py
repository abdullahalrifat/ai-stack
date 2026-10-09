"""Static guardrails for the production Compose deployment."""
from __future__ import annotations

from pathlib import Path
import re
import sys

compose = Path("docker-compose.yaml").read_text(encoding="utf-8")
services = re.split(r"(?m)^  ([a-zA-Z0-9_-]+):\s*$", compose)
service_blocks = {
    services[i]: services[i + 1]
    for i in range(1, len(services), 2)
}
errors: list[str] = []
for name in ("qdrant", "pipelines"):
    block = service_blocks.get(name, "")
    if re.search(r"(?m)^\s+ports:\s*$", block):
        errors.append(f"{name} is internal-only and must not publish host ports")

for name, block in service_blocks.items():
    image_match = re.search(r"(?m)^\s+image:\s*([^\s]+)", block)
    if not image_match or re.search(r"(?m)^\s+build:\s*", block):
        continue
    image = image_match.group(1)
    if image.endswith(":latest") or image.endswith(":main") or image.endswith(":dev"):
        errors.append(f"{name} uses a floating image tag: {image}")

if errors:
    print("\n".join(f"ERROR: {item}" for item in errors), file=sys.stderr)
    raise SystemExit(1)
print(f"Compose deployment guardrails passed for {len(service_blocks)} services.")
