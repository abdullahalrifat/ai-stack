#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
docker compose config --quiet
python -m pytest -q
python -m compileall -q server
bash -n scripts/*.sh
echo "world-class AI Stack checks: PASS"
