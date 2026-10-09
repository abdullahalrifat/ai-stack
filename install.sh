#!/usr/bin/env bash
set -Eeuo pipefail

# Build and deploy the current AI Stack source.
# This is intentionally generic: no version-specific or feature-specific
# checks belong here. The script should remain valid for future releases.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE=(docker compose)
BUILD_SERVICES=(server runs-ui)
DEPENDENCY_SERVICES=(postgres redis qdrant searxng)

log() {
  printf '[install] %s\n' "$*"
}

command -v docker >/dev/null 2>&1 || {
  echo "[install] ERROR: docker is not installed" >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  echo "[install] ERROR: Docker Compose v2 is required" >&2
  exit 1
}
[[ -f docker-compose.yaml ]] || {
  echo "[install] ERROR: docker-compose.yaml not found" >&2
  exit 1
}

log "validating Compose configuration"
"${COMPOSE[@]}" config --quiet

# Pull only registry-backed dependencies. Local application images must never
# be pulled: they are built from the current checkout below.
log "pulling latest dependency images"
"${COMPOSE[@]}" pull "${DEPENDENCY_SERVICES[@]}"

# Stop the application services before replacing their local images. Persistent
# volumes (Postgres, Redis, Qdrant, sandboxes, etc.) are not removed.
log "stopping current application services"
"${COMPOSE[@]}" rm --force --stop server agent-runner runs-ui >/dev/null 2>&1 || true

# Remove the old locally tagged application images. The next build recreates
# them from the current source and current base images.
log "removing old application images"
docker image rm -f ai-stack-server:latest ai-runs-ui:latest >/dev/null 2>&1 || true

# Equivalent to build.sh, but deliberately uncached so the current source and
# current base image are always used.
log "building latest application images without cache"
"${COMPOSE[@]}" build --pull --no-cache "${BUILD_SERVICES[@]}"

# Recreate all application containers so they cannot keep running an old image.
# agent-runner intentionally uses the same freshly built ai-stack-server image.
log "deploying freshly built images"
"${COMPOSE[@]}" up -d --force-recreate --remove-orphans   server agent-runner runs-ui

# Wait for Compose health checks before declaring the deployment complete.
log "waiting for services to become healthy"
"${COMPOSE[@]}" up -d --wait --wait-timeout "${HEALTH_TIMEOUT:-180}"   server agent-runner runs-ui

log "deployment complete"
"${COMPOSE[@]}" ps server agent-runner runs-ui
