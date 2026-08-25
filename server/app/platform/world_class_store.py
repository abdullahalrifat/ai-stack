"""v0.9 admission, capacity and capability-aware cloud task store."""

from __future__ import annotations

import uuid
from typing import Any

from .autonomous_store import AutonomousPlatformStore


class WorldClassPlatformStore(AutonomousPlatformStore):
    def active_for_tenant(self, tenant_id: str) -> int:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT COUNT(*) AS count FROM agent_cloud_tasks
                   WHERE status IN ('queued','running')
                     AND COALESCE(payload->>'tenant_id','default')=%s""",
                (tenant_id,),
            )
            row = cursor.fetchone()
            return int(row["count"] if row else 0)

    @staticmethod
    def _worker_matches(payload: dict[str, Any], capabilities: dict[str, Any]) -> bool:
        isolation = dict(payload.get("isolation") or {})
        mode = str(isolation.get("mode") or "trusted-host")
        if mode == "container" and not bool(capabilities.get("container")):
            return False
        if mode == "microvm" and not bool(capabilities.get("microvm")):
            return False
        resources = dict(payload.get("resources") or {})
        requested_cpu = float(resources.get("cpu") or 0)
        requested_memory = int(resources.get("memory_mb") or 0)
        if requested_cpu > float(capabilities.get("max_cpu") or 0):
            return False
        if requested_memory > int(capabilities.get("max_memory_mb") or 0):
            return False
        egress = dict(payload.get("egress") or {})
        if egress.get("hosts") and not bool(capabilities.get("egress_policy")):
            return False
        runtime = str(payload.get("runtime") or "auto")
        runtimes = {str(item) for item in capabilities.get("runtimes") or ["auto"]}
        return runtime == "auto" or runtime in runtimes

    def claim_cloud(
        self,
        worker_id: str,
        lease_seconds: int = 60,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        capabilities = dict(capabilities or {})
        capabilities.setdefault("max_cpu", 0)
        capabilities.setdefault("max_memory_mb", 0)
        lease = max(15, min(600, lease_seconds))
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id,payload FROM agent_cloud_tasks
                   WHERE (status='queued' AND execution_state='queued')
                      OR (status='running'
                          AND execution_state NOT IN ('cancel_requested','cancelled')
                          AND lease_expires_at<NOW())
                   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 32"""
            )
            rows = cursor.fetchall()
            selected = None
            for row in rows:
                payload = dict(row["payload"] or {})
                if self._worker_matches(payload, capabilities):
                    selected = row
                    break
            if selected is None:
                return None
            lease_id = str(uuid.uuid4())
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET status='running', execution_state='leased', worker_id=%s,
                       lease_id=%s, lease_expires_at=NOW()+(%s*INTERVAL '1 second'),
                       started_at=COALESCE(started_at,NOW()), attempts=attempts+1
                   WHERE id=%s RETURNING *""",
                (worker_id, lease_id, lease, selected["id"]),
            )
            return dict(cursor.fetchone())
