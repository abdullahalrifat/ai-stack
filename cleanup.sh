#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "=== AI Stack destructive cleanup ==="
echo "This removes this Compose project's containers and persistent bind-mounted data."
echo "It does NOT prune unrelated Docker resources."
read -r -p 'Type DELETE to continue: ' confirmation
[[ "$confirmation" == "DELETE" ]] || { echo "Aborted."; exit 0; }

docker compose down --remove-orphans
rm -rf ./postgres ./redis ./qdrant ./open-webui ./pipelines ./agent-sandboxes ./searxng/data
docker image rm -f ai-runs-ui:latest ai-stack-server:latest 2>/dev/null || true

echo "Cleanup complete. .env and unrelated Docker resources were preserved."
