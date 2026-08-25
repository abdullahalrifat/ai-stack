"""v0.8 cloud-task state-machine hardening around the platform store."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from .store import PlatformStore as BasePlatformStore


_PROOF_SCHEMA_VERSION = 1


def _sha256_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _validate_execution_proof(
    proof: Any, *, task_id: str, lease_id: str
) -> int | None:
    """Validate a successful completion proof and return its fenced attempt."""

    if not isinstance(proof, dict) or proof.get("schema_version") != _PROOF_SCHEMA_VERSION:
        return None
    if proof.get("task_id") != task_id or proof.get("lease_id") != lease_id:
        return None
    if not all(str(proof.get(key) or "").strip() for key in ("route", "model")):
        return None
    if not _sha256_digest(proof.get("workspace_digest")):
        return None
    if not _sha256_digest(proof.get("mutation_digest")):
        return None
    try:
        attempt = int(proof.get("attempt", 0))
    except (TypeError, ValueError):
        return None
    if attempt < 1:
        return None
    checks = proof.get("verifications")
    if not isinstance(checks, list) or not checks:
        return None
    for check in checks:
        if (
            not isinstance(check, dict)
            or not str(check.get("command") or "").strip()
            or check.get("status") != "passed"
            or check.get("exit_code") != 0
            or not _sha256_digest(check.get("output_digest"))
        ):
            return None
    artifacts = proof.get("artifact_hashes", {})
    if not isinstance(artifacts, dict) or any(
        not str(name).strip() or not _sha256_digest(digest)
        for name, digest in artifacts.items()
    ):
        return None
    return attempt

_WORKER_STATES = {
    "preparing_workspace",
    "running",
    "verifying",
    "uploading_result",
}
_ALLOWED_WORKER_TRANSITIONS = {
    "leased": {"preparing_workspace"},
    "preparing_workspace": {"running"},
    "running": {"verifying"},
    "verifying": {"uploading_result"},
}


class AutonomousPlatformStore(BasePlatformStore):
    def submit_cloud(
        self,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        with self.runs.connection() as connection, connection.cursor() as cursor:
            if idempotency_key:
                cursor.execute(
                    """INSERT INTO agent_cloud_tasks
                       (id,payload,idempotency_key,execution_state)
                       VALUES(%s,%s,%s,'queued')
                       ON CONFLICT (idempotency_key)
                       WHERE idempotency_key IS NOT NULL
                       DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key
                       RETURNING *""",
                    (task_id, Jsonb(payload), idempotency_key),
                )
            else:
                cursor.execute(
                    """INSERT INTO agent_cloud_tasks(id,payload,execution_state)
                       VALUES(%s,%s,'queued') RETURNING *""",
                    (task_id, Jsonb(payload)),
                )
            row = dict(cursor.fetchone())
            if idempotency_key and dict(row.get("payload") or {}) != payload:
                raise ValueError(
                    "idempotency key is already bound to a different cloud task payload"
                )
            return row

    def claim_cloud(
        self, worker_id: str, lease_seconds: int = 60
    ) -> dict[str, Any] | None:
        lease = max(15, min(600, lease_seconds))
        lease_id = str(uuid.uuid4())
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id FROM agent_cloud_tasks
                   WHERE (status='queued' AND execution_state='queued')
                      OR (status='running'
                          AND execution_state NOT IN ('cancel_requested','cancelled')
                          AND lease_expires_at<NOW())
                   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET status='running', execution_state='leased', worker_id=%s,
                       lease_id=%s, lease_expires_at=NOW()+(%s*INTERVAL '1 second'),
                       started_at=COALESCE(started_at,NOW()), attempts=attempts+1
                   WHERE id=%s RETURNING *""",
                (worker_id, lease_id, lease, row["id"]),
            )
            return dict(cursor.fetchone())

    def heartbeat_cloud(
        self,
        task_id: str,
        worker_id: str,
        lease_id: str,
        lease_seconds: int = 60,
    ) -> bool:
        lease = max(15, min(600, lease_seconds))
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET lease_expires_at=NOW()+(%s*INTERVAL '1 second')
                   WHERE id=%s AND status='running' AND worker_id=%s
                     AND lease_id=%s AND lease_expires_at>=NOW()""",
                (lease, task_id, worker_id, lease_id),
            )
            return cursor.rowcount == 1

    def update_cloud_state(
        self,
        task_id: str,
        worker_id: str,
        lease_id: str,
        state: str,
        proof: dict[str, Any] | None = None,
    ) -> bool:
        if state not in _WORKER_STATES:
            raise ValueError(f"unsupported worker execution state: {state}")
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT execution_state FROM agent_cloud_tasks
                   WHERE id=%s AND status='running' AND worker_id=%s AND lease_id=%s
                     AND lease_expires_at>=NOW() FOR UPDATE""",
                (task_id, worker_id, lease_id),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            current = str(row["execution_state"])
            if state not in _ALLOWED_WORKER_TRANSITIONS.get(current, set()):
                raise ValueError(
                    f"invalid cloud execution transition: {current} -> {state}"
                )
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET execution_state=%s, proof=COALESCE(%s,proof)
                   WHERE id=%s AND status='running' AND worker_id=%s AND lease_id=%s
                     AND lease_expires_at>=NOW()""",
                (
                    state,
                    Jsonb(proof) if proof is not None else None,
                    task_id,
                    worker_id,
                    lease_id,
                ),
            )
            return cursor.rowcount == 1

    def finish_cloud(
        self,
        task_id: str,
        worker_id: str,
        lease_id: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        proof: dict[str, Any] | None = None,
    ) -> bool:
        status = "failed" if error else "completed"
        # A successful result is publishable only after the worker has entered
        # uploading_result and supplied a non-empty execution proof. Failures
        # remain publishable from any fenced active state for diagnostics.
        proof_attempt = None if error else _validate_execution_proof(
            proof, task_id=task_id, lease_id=lease_id
        )
        completion_has_proof = bool(error) or proof_attempt is not None
        failure_completion = bool(error)
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET status=%s, execution_state=%s, result=%s, error=%s,
                       proof=COALESCE(%s,proof), completed_at=NOW(),
                       lease_expires_at=NULL, lease_id=NULL
                   WHERE id=%s AND status='running' AND worker_id=%s
                     AND lease_id=%s AND lease_expires_at>=NOW()
                     AND %s
                     AND (%s OR execution_state='uploading_result')
                     AND (%s OR attempts=%s)""",
                (
                    status,
                    status,
                    Jsonb(result or {}),
                    error[:4000] if error else None,
                    Jsonb(proof) if proof is not None else None,
                    task_id,
                    worker_id,
                    lease_id,
                    completion_has_proof,
                    failure_completion,
                    failure_completion,
                    proof_attempt or 0,
                ),
            )
            return cursor.rowcount == 1

    def cancel_cloud(self, task_id: str) -> bool:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET status='cancelled', execution_state='cancelled', completed_at=NOW(),
                       lease_expires_at=NULL, lease_id=NULL
                   WHERE id=%s AND status IN ('queued','running')""",
                (task_id,),
            )
            return cursor.rowcount == 1
