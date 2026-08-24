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
        cursor.execute(
            """INSERT INTO agent_failure_run_observations(run_id,fingerprint)
               VALUES(%s,%s) ON CONFLICT(run_id) DO NOTHING""",
            (run_id, fingerprint),
        )
        inserted = cursor.rowcount == 1
        if not inserted:
            return False
        cursor.execute(
            """INSERT INTO agent_failure_signatures
               (fingerprint,kind,category,route,detail,recovery,first_run_id,last_run_id)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(fingerprint) DO UPDATE SET
                 occurrences=agent_failure_signatures.occurrences+1,
                 last_run_id=EXCLUDED.last_run_id,
                 last_seen=NOW(),
                 recovery=COALESCE(EXCLUDED.recovery,agent_failure_signatures.recovery),
                 route=COALESCE(EXCLUDED.route,agent_failure_signatures.route)""",
            (
                fingerprint,
                kind,
                category,
                route,
                detail[:4000],
                recovery,
                run_id,
                run_id,
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
