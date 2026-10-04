#!/usr/bin/env bash

set -e

CONTAINER="ollama"

MODELS=(
  "qwen3:4b"
  "qwen3:8b"
  "nomic-embed-text"
)

echo "Waiting for Ollama..."

until docker exec "$CONTAINER" ollama list >/dev/null 2>&1; do
    sleep 2
done

echo "Ollama is ready."

for MODEL in "${MODELS[@]}"; do
    echo
    echo "======================================"
    echo "Pulling $MODEL"
    echo "======================================"

    docker exec "$CONTAINER" ollama pull "$MODEL"
done

echo
echo "Installed models:"
docker exec "$CONTAINER" ollama list