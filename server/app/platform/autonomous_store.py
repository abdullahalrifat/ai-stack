"""v0.8 cloud-task state-machine hardening around the platform store."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from .store import PlatformStore as BasePlatformStore

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
            return dict(cursor.fetchone())

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
                raise ValueError(f"invalid cloud execution transition: {current} -> {state}")
            cursor.execute(
                """UPDATE agent_cloud_tasks SET execution_state=%s, proof=COALESCE(%s,proof)
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
