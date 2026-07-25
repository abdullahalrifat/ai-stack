#!/usr/bin/env bash

set -euo pipefail

# Build in place.  Do not delete existing images: they remain a useful rollback
# target if a new build or deployment fails.
docker build -t ai-agents:latest ./agents
docker build -t custom-litellm:latest ./litellm

