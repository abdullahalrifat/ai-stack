"""v0.8 cloud-task state-machine hardening around the platform store."""

from __future__ import annotations

import uuid
from typing import Any

from .store import PlatformStore as BasePlatformStore


class AutonomousPlatformStore(BasePlatformStore):
    def claim_cloud(
        self, worker_id: str, lease_seconds: int = 60
    ) -> dict[str, Any] | None:
        lease = max(15, min(600, lease_seconds))
        lease_id = str(uuid.uuid4())
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id FROM agent_cloud_tasks
                   WHERE (status='queued' AND execution_state='queued')
                      OR (status='running' AND execution_state NOT IN ('cancel_requested','cancelled')
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
