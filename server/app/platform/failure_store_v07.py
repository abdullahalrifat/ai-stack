"""Persistent structured failure memory for v0.7."""

from __future__ import annotations

import hashlib
from typing import Any

from .store import PlatformStore

_INSTALLED = False


def _record_failure_signature(
    self,
    *,
    run_id: str,
    fingerprint: str,
    kind: str,
    category: str,
    route: str | None,
    detail: str,
    recovery: str | None,
) -> bool:
    with self.runs.connection() as connection, connection.cursor() as cursor:
        # Seed the referenced signature before the per-run observation so the
        # foreign key is valid even for the first occurrence. Occurrences start
        # at zero and are incremented only after this run claims its idempotency
        # row successfully.
        cursor.execute(
            """INSERT INTO agent_failure_signatures
               (fingerprint,kind,category,route,detail,recovery,occurrences)
               VALUES(%s,%s,%s,%s,%s,%s,0)
               ON CONFLICT(fingerprint) DO NOTHING""",
            (fingerprint, kind, category, route, detail[:4000], recovery),
        )
        cursor.execute(
            """INSERT INTO agent_failure_run_observations(run_id,fingerprint)
               VALUES(%s,%s) ON CONFLICT(run_id) DO NOTHING""",
            (run_id, fingerprint),
        )
        inserted = cursor.rowcount == 1
        if not inserted:
            return False
        cursor.execute(
            """UPDATE agent_failure_signatures SET
                 occurrences=occurrences+1,
                 first_run_id=COALESCE(first_run_id,%s),
                 last_run_id=%s,
                 last_seen=NOW(),
                 detail=%s,
                 recovery=COALESCE(%s,recovery),
                 route=COALESCE(%s,route),
                 category=%s,
                 kind=%s
               WHERE fingerprint=%s""",
            (
                run_id,
                run_id,
                detail[:4000],
                recovery,
                route,
                category,
                kind,
                fingerprint,
            ),
        )
        return True


def _failure_hints(self, category: str, limit: int = 10) -> list[dict[str, Any]]:
    with self.runs.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT kind,route,recovery,occurrences,last_seen
               FROM agent_failure_signatures
               WHERE category IN (%s,'code','*')
               ORDER BY occurrences DESC,last_seen DESC
               LIMIT %s""",
            (category, max(1, min(50, limit))),
        )
        return [dict(row) for row in cursor.fetchall()]


def install_failure_store() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    PlatformStore.record_failure_signature = _record_failure_signature
    PlatformStore.failure_hints = _failure_hints
    _INSTALLED = True


def failure_fingerprint(kind: str, route: str | None, detail: str) -> str:
    normalized = " ".join(detail.casefold().split())[:1200]
    return hashlib.sha256(f"{kind}|{route or ''}|{normalized}".encode()).hexdigest()[:24]
