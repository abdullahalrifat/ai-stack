#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail() { echo "ERROR: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

command -v docker >/dev/null || fail "Docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
[[ -f .env ]] || fail ".env is missing; copy .env.example to .env"

set -a
source .env
set +a

[[ -n "${INFERENCE_BASE_URL:-}" ]] || fail "INFERENCE_BASE_URL is required"
[[ -n "${INFERENCE_API_KEY:-}" ]] || fail "INFERENCE_API_KEY is required"
[[ -n "${AGENT_API_KEY:-}" ]] || fail "AGENT_API_KEY is required"
[[ -n "${RUNNER_API_KEY:-}" ]] || fail "RUNNER_API_KEY is required"
[[ -n "${POSTGRES_PASSWORD:-}" ]] || fail "POSTGRES_PASSWORD is required"
[[ "${INFERENCE_BASE_URL}" == */v1 ]] || fail "INFERENCE_BASE_URL must end in /v1"

ok "Docker and Compose available"
ok "Inference gateway configured: ${INFERENCE_BASE_URL}"
ok "Required secrets configured"

curl_args=(-fsS --max-time 5)
if [[ -n "${INFERENCE_API_KEY:-}" ]]; then
  curl_args+=(-H "Authorization: Bearer ${INFERENCE_API_KEY}")
fi
health="${INFERENCE_BASE_URL%/v1}/ready"
models="${INFERENCE_BASE_URL}/models"
capabilities="${INFERENCE_BASE_URL}/capabilities"

curl "${curl_args[@]}" "$health" >/tmp/jarvis-inference-ready.json || fail "jarvis-inference is not ready at ${health}"
curl "${curl_args[@]}" "$models" >/tmp/jarvis-inference-models.json || fail "jarvis-inference model endpoint is unavailable"
curl "${curl_args[@]}" "$capabilities" >/tmp/jarvis-inference-capabilities.json || fail "jarvis-inference capability endpoint is unavailable"

python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("/tmp/jarvis-inference-models.json").read_text())
models = {item["id"] for item in data.get("data", [])}
required = {"qwen3:1.7b", "qwen3:4b"}
missing = sorted(required - models)
if missing:
    raise SystemExit(f"Missing inference models: {', '.join(missing)}")
print("OK: required chat models are available")

cap = json.loads(Path("/tmp/jarvis-inference-capabilities.json").read_text())
if cap.get("protocol", {}).get("current") != 1:
    raise SystemExit("Unsupported jarvis-inference protocol version")
features = set(cap.get("features", []))
required_features = {"chat", "streaming", "embeddings", "model_catalog", "request_ids"}
missing_features = sorted(required_features - features)
if missing_features:
    raise SystemExit(f"Missing inference capabilities: {', '.join(missing_features)}")
print("OK: inference protocol v1 and required capabilities are available")
limits = cap.get("limits", {})
embedding_timeout = float(limits.get("embedding_timeout_seconds", 0))
configured_embedding_timeout = float(__import__("os").environ.get("EMBEDDING_TIMEOUT_SECONDS", "45"))
if embedding_timeout <= 0 or configured_embedding_timeout < embedding_timeout:
    raise SystemExit("AI Stack EMBEDDING_TIMEOUT_SECONDS must be >= inference embedding timeout")
print("OK: embedding timeout hierarchy is valid")
chat_timeout = float(limits.get("chat_timeout_seconds", 0))
configured_llm_timeout = float(__import__("os").environ.get("LLM_TIMEOUT_SECONDS", "180"))
if chat_timeout <= 0 or configured_llm_timeout > chat_timeout:
    raise SystemExit("AI Stack LLM_TIMEOUT_SECONDS must be <= inference chat timeout")
print("OK: chat timeout hierarchy is valid")
research_timeout = float(__import__("os").environ.get("RESEARCH_TIMEOUT_SECONDS", "300"))
if research_timeout > chat_timeout:
    raise SystemExit("RESEARCH_TIMEOUT_SECONDS must be <= inference chat timeout")
print("OK: research timeout hierarchy is valid")
PY

curl "${curl_args[@]}" -H "Content-Type: application/json" \
  -d '{"model":"nomic-embed-text","input":"inference preflight"}' \
  "${INFERENCE_BASE_URL}/embeddings" >/tmp/jarvis-inference-embedding.json \
  || fail "nomic-embed-text embedding endpoint is unavailable"

python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("/tmp/jarvis-inference-embedding.json").read_text())
items = data.get("data") or []
if not items or not items[0].get("embedding"):
    raise SystemExit("Inference gateway returned no embedding vector")
print("OK: embedding model is available")
PY

docker compose config --quiet
ok "Compose configuration is valid"
ok "Preflight passed"
