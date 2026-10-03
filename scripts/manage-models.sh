#!/usr/bin/env bash
# Manage locally installed Ollama models without deleting any model files.
# Run from the repository root: ./scripts/manage-models.sh <command> [model]
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/manage-models.sh list
  ./scripts/manage-models.sh active
  ./scripts/manage-models.sh enable <model>
  ./scripts/manage-models.sh disable <model>

Aliases: qwen3-8b, nomic-embed-text.

`enable` downloads the underlying model if needed. `disable` only unloads it
from RAM; it does not delete model files. Use `list` to see downloaded models
and `active` to see RAM-resident models.
EOF
}

model_name() {
  case "${1:-}" in
    qwen3-8b) echo "qwen3:8b" ;;
    nomic-embed-text) echo "nomic-embed-text" ;;
    *) echo "Unknown model alias: ${1:-}" >&2; usage >&2; exit 2 ;;
  esac
}

case "${1:-}" in
  list) docker compose exec -T ollama ollama list ;;
  active) docker compose exec -T ollama ollama ps ;;
  enable) docker compose exec -T ollama ollama pull "$(model_name "${2:-}")" ;;
  disable) docker compose exec -T ollama ollama stop "$(model_name "${2:-}")" || true ;;
  *) usage; exit 2 ;;
esac
