#!/usr/bin/env bash
set -Eeuo pipefail

# Production installer/deployer for the AI Stack.
#
# Guarantees:
#   1. Deployment checkout is fast-forwarded to origin/main.
#   2. Registry-backed dependency images are refreshed.
#   3. Server/UI images are rebuilt with fresh base images and no cache.
#   4. Superseded image IDs are removed after a successful build.
#   5. Images are tagged with the Git SHA and latest, then pushed.
#   6. Containers are replaced only after build and push succeed.
#   7. Compose health checks and the inference capability contract are verified.

SCRIPT_DIR="$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
COMPOSE=(docker compose)
SERVER_CONTAINER="ai-stack-server"
RUNNER_CONTAINER="agent-runner"
UI_CONTAINER="ai-runs-ui"

SERVER_LOCAL_IMAGE="ai-stack-server:latest"
UI_LOCAL_IMAGE="ai-runs-ui:latest"

AI_STACK_REGISTRY="\${AI_STACK_REGISTRY:-ghcr.io/abdullahalrifat}"
SERVER_REMOTE_IMAGE="\${AI_STACK_SERVER_IMAGE:-\${AI_STACK_REGISTRY}/ai-stack-server}"
UI_REMOTE_IMAGE="\${AI_STACK_UI_IMAGE:-\${AI_STACK_REGISTRY}/ai-runs-ui}"

PUSH_IMAGES="\${PUSH_IMAGES:-true}"
HEALTH_TIMEOUT="\${HEALTH_TIMEOUT:-180}"
SKIP_GIT_UPDATE="\${SKIP_GIT_UPDATE:-false}"

log() {
  printf '[install] %s\n' "$*"
}

fail() {
  printf '[install] ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup_on_error() {
  local exit_code=$?
  if (( exit_code != 0 )); then
    log "installation failed with exit code \${exit_code}"
    log "current application status:"
    "\${COMPOSE[@]}" ps "$SERVER_CONTAINER" "$RUNNER_CONTAINER" "$UI_CONTAINER" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap cleanup_on_error EXIT

cd "$PROJECT_ROOT"

command -v git >/dev/null 2>&1 || fail "git is not installed"
command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
[[ -f docker-compose.yaml ]] || fail "docker-compose.yaml is missing"
[[ -f .env ]] || fail ".env is missing; configure it before installation"

if [[ "$SKIP_GIT_UPDATE" != true ]]; then
  current_branch="$(git branch --show-current)"
  [[ "$current_branch" == "main" ]] || fail "install.sh must run from main; current branch is '$current_branch'"
  [[ -z "$(git status --porcelain)" ]] || fail "working tree is dirty; commit/stash local changes before installation"

  log "fetching origin/main"
  git fetch --prune origin main

  log "fast-forwarding deployment checkout to origin/main"
  git merge --ff-only origin/main
fi

GIT_SHA="$(git rev-parse HEAD)"
GIT_SHORT_SHA="$(git rev-parse --short=12 HEAD)"
log "deploying commit \${GIT_SHORT_SHA}"

log "validating Compose configuration"
"\${COMPOSE[@]}" config --quiet

# Refresh registry-backed services. Buildable services are deliberately
# excluded because they must be rebuilt locally from the checked-out source.
log "pulling latest registry-backed dependency images"
"\${COMPOSE[@]}" pull --ignore-buildable

old_server_id="$(docker inspect --format '{{.Image}}' "$SERVER_CONTAINER" 2>/dev/null || true)"
old_ui_id="$(docker inspect --format '{{.Image}}' "$UI_CONTAINER" 2>/dev/null || true)"

log "building AI Stack server with fresh base images and no cache"
"\${COMPOSE[@]}" build --pull --no-cache server

log "building Runs UI with fresh base images and no cache"
"\${COMPOSE[@]}" build --pull --no-cache runs-ui

new_server_id="$(docker image inspect --format '{{.Id}}' "$SERVER_LOCAL_IMAGE")"
new_ui_id="$(docker image inspect --format '{{.Id}}' "$UI_LOCAL_IMAGE")"

[[ -n "$new_server_id" ]] || fail "server image was not produced"
[[ -n "$new_ui_id" ]] || fail "runs-ui image was not produced"

log "verifying server image contains the current capability contract"
server_feature="$(
  docker run --rm --entrypoint python "$SERVER_LOCAL_IMAGE" -c \
    'from app.api.protocol import FEATURES; print("inference_diagnostics" in FEATURES)'
)"
[[ "$server_feature" == "True" ]] || fail "new server image is missing inference_diagnostics"

if [[ "$PUSH_IMAGES" == true ]]; then
  log "tagging immutable Git SHA images"
  docker tag "$SERVER_LOCAL_IMAGE" "\${SERVER_REMOTE_IMAGE}:\${GIT_SHA}"
  docker tag "$UI_LOCAL_IMAGE" "\${UI_REMOTE_IMAGE}:\${GIT_SHA}"

  log "tagging latest images"
  docker tag "$SERVER_LOCAL_IMAGE" "\${SERVER_REMOTE_IMAGE}:latest"
  docker tag "$UI_LOCAL_IMAGE" "\${UI_REMOTE_IMAGE}:latest"

  log "pushing server image \${SERVER_REMOTE_IMAGE}:\${GIT_SHA}"
  docker push "\${SERVER_REMOTE_IMAGE}:\${GIT_SHA}"
  log "pushing server image \${SERVER_REMOTE_IMAGE}:latest"
  docker push "\${SERVER_REMOTE_IMAGE}:latest"

  log "pushing UI image \${UI_REMOTE_IMAGE}:\${GIT_SHA}"
  docker push "\${UI_REMOTE_IMAGE}:\${GIT_SHA}"
  log "pushing UI image \${UI_REMOTE_IMAGE}:latest"
  docker push "\${UI_REMOTE_IMAGE}:latest"
else
  log "PUSH_IMAGES=false; skipping registry push"
fi

# The build succeeded, so it is now safe to replace the running containers.
# Existing containers retain their old image IDs even after latest is retagged.
log "stopping old application containers"
"\${COMPOSE[@]}" rm --force --stop "$SERVER_CONTAINER" "$RUNNER_CONTAINER" "$UI_CONTAINER" >/dev/null

if [[ -n "$old_server_id" && "$old_server_id" != "$new_server_id" ]]; then
  log "removing superseded server image \${old_server_id}"
  docker image rm "$old_server_id" >/dev/null 2>&1 || log "old server image is still referenced; leaving it in place"
fi

if [[ -n "$old_ui_id" && "$old_ui_id" != "$new_ui_id" ]]; then
  log "removing superseded UI image \${old_ui_id}"
  docker image rm "$old_ui_id" >/dev/null 2>&1 || log "old UI image is still referenced; leaving it in place"
fi

log "starting the freshly built application containers"
"\${COMPOSE[@]}" up -d --force-recreate --remove-orphans "$SERVER_CONTAINER" "$RUNNER_CONTAINER" "$UI_CONTAINER"

log "waiting up to \${HEALTH_TIMEOUT}s for application health"
if ! "\${COMPOSE[@]}" wait --timeout "$HEALTH_TIMEOUT" "$SERVER_CONTAINER" "$RUNNER_CONTAINER" "$UI_CONTAINER"; then
  log "Compose wait did not report success; collecting logs"
  "\${COMPOSE[@]}" logs --tail=100 "$SERVER_CONTAINER" "$RUNNER_CONTAINER" "$UI_CONTAINER" || true
  fail "application health check failed"
fi

server_runtime_id="$(docker inspect --format '{{.Image}}' "$SERVER_CONTAINER")"
runner_runtime_id="$(docker inspect --format '{{.Image}}' "$RUNNER_CONTAINER")"
ui_runtime_id="$(docker inspect --format '{{.Image}}' "$UI_CONTAINER")"

[[ "$server_runtime_id" == "$new_server_id" ]] || fail "server container is not using the newly built image"
[[ "$runner_runtime_id" == "$new_server_id" ]] || fail "agent-runner is not using the newly built server image"
[[ "$ui_runtime_id" == "$new_ui_id" ]] || fail "Runs UI is not using the newly built image"

runtime_feature="$(
  docker exec "$SERVER_CONTAINER" python -c \
    'from app.api.protocol import FEATURES; print("inference_diagnostics" in FEATURES)'
)"
[[ "$runtime_feature" == "True" ]] || fail "running server is missing inference_diagnostics"

log "removing dangling build artifacts"
docker image prune -f >/dev/null

log "installation complete"
log "commit:       \${GIT_SHA}"
log "server image: \${new_server_id}"
log "UI image:     \${new_ui_id}"
log "inference_diagnostics: enabled"
"\${COMPOSE[@]}" ps "$SERVER_CONTAINER" "$RUNNER_CONTAINER" "$UI_CONTAINER"
