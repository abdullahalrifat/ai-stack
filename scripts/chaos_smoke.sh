#!/usr/bin/env bash
set -euo pipefail
set -x
docker compose ps
curl --fail --silent http://127.0.0.1:8081/health >/dev/null
docker compose restart agent-runner
sleep 5
curl --fail --silent http://127.0.0.1:8081/health >/dev/null
docker compose restart server
sleep 5
curl --fail --silent http://127.0.0.1:8081/health >/dev/null
docker compose restart redis
sleep 5
docker compose ps
curl --fail --silent http://127.0.0.1:8081/health >/dev/null
echo "basic restart/recovery chaos smoke: PASS"
