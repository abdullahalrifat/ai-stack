#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

bash scripts/preflight.sh

echo "Building AI Stack images..."
docker compose build --pull server runs-ui agent-runner

echo "Starting AI Stack..."
docker compose up -d --remove-orphans

echo "Waiting for services..."
docker compose up --wait

echo "Deployment status:"
docker compose ps

echo "Server health:"
curl -fsS http://127.0.0.1:8081/health
echo

echo "Deployment completed successfully."
