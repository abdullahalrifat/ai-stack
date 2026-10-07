#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/ai-stack-$STAMP.sql.gz"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-ai_stack}" "${POSTGRES_DB:-ai_stack}" | gzip > "$OUT"
gzip -t "$OUT"
test -s "$OUT"
echo "backup verified: $OUT"
