#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE=(docker compose)
AGENT_SERVICES=(agent-runner agents)
HEALTH_TIMEOUT=120
RUN_TESTS=true
NO_CACHE=false
REMOVE_OLD_IMAGE=true

usage() {
  cat <<'EOF'
Usage: ./scripts/release.sh [options]

Build, test, and deploy the AI Stack agent services. The new image is built
before the old containers are stopped. A failed deployment is automatically
rolled back to the previously deployed image.

Options:
  --skip-tests       Do not run the complete agent and CLI test suite.
  --no-cache         Build the agent image without Docker layer cache.
  --keep-old-image   Keep the superseded agent image after a healthy deploy.
  --timeout SECONDS  Health-check timeout (default: 120).
  -h, --help         Show this help.
EOF
}

log() {
  printf '[release] %s\n' "$*"
}

fail() {
  printf '[release] ERROR: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --skip-tests)
      RUN_TESTS=false
      ;;
    --no-cache)
      NO_CACHE=true
      ;;
    --keep-old-image)
      REMOVE_OLD_IMAGE=false
      ;;
    --timeout)
      shift
      (($#)) || fail "--timeout requires a value"
      [[ "$1" =~ ^[1-9][0-9]*$ ]] || fail "--timeout must be a positive integer"
      HEALTH_TIMEOUT="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
  shift
done

cd "$PROJECT_ROOT"

command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
[[ -f .env ]] || fail ".env is missing; copy .env.example and configure it first"

log "validating Compose configuration"
"${COMPOSE[@]}" config --quiet

old_image_id="$(docker inspect --format '{{.Image}}' ai-agents 2>/dev/null || true)"
build_args=(build)
if [[ "$NO_CACHE" == true ]]; then
  build_args+=(--no-cache)
fi
build_args+=(agents)

log "building ai-agents:latest"
"${COMPOSE[@]}" "${build_args[@]}"
new_image_id="$(docker image inspect --format '{{.Id}}' ai-agents:latest)"
[[ -n "$new_image_id" ]] || fail "the new ai-agents image was not created"

if [[ "$RUN_TESTS" == true ]]; then
  log "running complete agent and CLI test suite against the new image"
  docker run --rm \
    --volume "${PROJECT_ROOT}:/workspace" \
    --workdir /workspace \
    --env PYTHONPATH=/workspace/agents:/workspace/jarvis/src \
    ai-agents:latest \
    pytest -q server/tests jarvis/tests
fi

rollback() {
  if [[ -z "$old_image_id" ]]; then
    log "no previous image is available for rollback"
    return 1
  fi
  if [[ "$old_image_id" != "$new_image_id" ]]; then
    log "rolling back to ${old_image_id}"
    docker tag "$old_image_id" ai-agents:latest
  else
    log "restarting the previously deployed image"
  fi
  "${COMPOSE[@]}" rm --force --stop "${AGENT_SERVICES[@]}" >/dev/null 2>&1 || true
  "${COMPOSE[@]}" up -d --force-recreate "${AGENT_SERVICES[@]}"
}

deployment_failed() {
  log "deployment failed; recent service logs follow"
  "${COMPOSE[@]}" logs --tail=120 "${AGENT_SERVICES[@]}" || true
  rollback || true
  exit 1
}

log "stopping and removing old agent containers"
"${COMPOSE[@]}" rm --force --stop "${AGENT_SERVICES[@]}"

log "starting fresh agent containers"
if ! "${COMPOSE[@]}" up -d --force-recreate "${AGENT_SERVICES[@]}"; then
  deployment_failed
fi

log "waiting up to ${HEALTH_TIMEOUT}s for the agent API"
deadline=$((SECONDS + HEALTH_TIMEOUT))
while ((SECONDS < deadline)); do
  api_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-agents 2>/dev/null || true)"
  runner_status="$(docker inspect --format '{{.State.Status}}' agent-runner 2>/dev/null || true)"
  if [[ "$api_health" == healthy && "$runner_status" == running ]]; then
    break
  fi
  sleep 2
done

api_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ai-agents 2>/dev/null || true)"
runner_status="$(docker inspect --format '{{.State.Status}}' agent-runner 2>/dev/null || true)"
[[ "$api_health" == healthy && "$runner_status" == running ]] || deployment_failed

deployed_api_image="$(docker inspect --format '{{.Image}}' ai-agents)"
deployed_runner_image="$(docker inspect --format '{{.Image}}' agent-runner)"
if [[ "$deployed_api_image" != "$new_image_id" || "$deployed_runner_image" != "$new_image_id" ]]; then
  log "deployed containers do not use the newly built image"
  deployment_failed
fi

if [[ "$REMOVE_OLD_IMAGE" == true && -n "$old_image_id" && "$old_image_id" != "$new_image_id" ]]; then
  log "removing superseded agent image ${old_image_id}"
  docker image rm "$old_image_id" >/dev/null 2>&1 || log "old image is still in use; leaving it in place"
fi

log "deployment healthy"
"${COMPOSE[@]}" ps "${AGENT_SERVICES[@]}"
log "deployed image: ${new_image_id}"
