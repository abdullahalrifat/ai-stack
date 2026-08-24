"""Durable Postgres-backed scheduling, cloud leasing, and calibration storage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from app.runs.store import get_run_store


def _field_values(expression: str, minimum: int, maximum: int, *, sunday_7: bool = False) -> set[int]:
    raw_max = 7 if sunday_7 else maximum
    values: set[int] = set()
    for raw in expression.split(","):
        part = raw.strip()
        if not part:
            raise ValueError("empty cron field")
        step = 1
        base = part
        if "/" in part:
            base, step_raw = part.split("/", 1)
            step = int(step_raw)
            if step <= 0:
                raise ValueError("cron step must be positive")
        if base == "*":
            start, end = minimum, raw_max
        elif "-" in base:
            start, end = (int(item) for item in base.split("-", 1))
        else:
            start = end = int(base)
        if start > end or start < minimum or end > raw_max:
            raise ValueError("cron value out of range")
        values.update(range(start, end + 1, step))
    return {0 if sunday_7 and item == 7 else item for item in values}


def cron_matches(expression: str, value: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron must have 5 fields: minute hour day month weekday")
    minute, hour, dom, month, dow = fields
    value = value.astimezone(timezone.utc)
    weekday = (value.weekday() + 1) % 7
    if value.minute not in _field_values(minute, 0, 59):
        return False
    if value.hour not in _field_values(hour, 0, 23):
        return False
    if value.month not in _field_values(month, 1, 12):
        return False
    dom_match = value.day in _field_values(dom, 1, 31)
    dow_match = weekday in _field_values(dow, 0, 6, sunday_7=True)
    if dom == "*" and dow == "*":
        return True
    if dom == "*":
        return dow_match
    if dow == "*":
        return dom_match
    return dom_match or dow_match


def next_cron(expression: str, after: datetime) -> datetime:
    candidate = after.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 24 * 366 * 2):
        if cron_matches(expression, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("cron has no occurrence within two years")


class PlatformStore:
    def __init__(self) -> None:
        self.runs = get_run_store()

    def create_schedule(self, *, name: str, payload: dict[str, Any], interval_seconds: int | None = None, cron: str | None = None) -> dict[str, Any]:
        if bool(interval_seconds) == bool(cron):
            raise ValueError("choose exactly one of interval_seconds or cron")
        if interval_seconds is not None and interval_seconds < 60:
            raise ValueError("minimum interval is 60 seconds")
        now = datetime.now(timezone.utc)
        next_run = now + timedelta(seconds=interval_seconds) if interval_seconds else next_cron(str(cron), now)
        schedule_id = str(uuid.uuid4())
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO agent_platform_schedules
                   (id,name,payload,interval_seconds,cron,next_run)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                (schedule_id, name, Jsonb(payload), interval_seconds, cron, next_run),
            )
            return dict(cursor.fetchone())

    def list_schedules(self) -> list[dict[str, Any]]:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM agent_platform_schedules ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> bool:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE agent_platform_schedules SET enabled=%s WHERE id=%s", (enabled, schedule_id))
            return cursor.rowcount == 1

    def due_schedules(self, limit: int = 20) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        claimed: list[dict[str, Any]] = []
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT * FROM agent_platform_schedules
                   WHERE enabled=TRUE AND next_run<=NOW()
                   ORDER BY next_run FOR UPDATE SKIP LOCKED LIMIT %s""",
                (limit,),
            )
            for row in cursor.fetchall():
                payload = dict(row)
                next_run = now + timedelta(seconds=int(row["interval_seconds"])) if row["interval_seconds"] is not None else next_cron(str(row["cron"]), now)
                cursor.execute("UPDATE agent_platform_schedules SET last_run=NOW(), next_run=%s WHERE id=%s", (next_run, row["id"]))
                claimed.append(payload)
        return claimed

    def submit_cloud(self, payload: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        with self.runs.connection() as connection, connection.cursor() as cursor:
            if idempotency_key:
                cursor.execute("SELECT * FROM agent_cloud_tasks WHERE idempotency_key=%s", (idempotency_key,))
                existing = cursor.fetchone()
                if existing:
                    return dict(existing)
            cursor.execute(
                """INSERT INTO agent_cloud_tasks(id,payload,idempotency_key,execution_state)
                   VALUES(%s,%s,%s,'queued') RETURNING *""",
                (task_id, Jsonb(payload), idempotency_key),
            )
            return dict(cursor.fetchone())

    def claim_cloud(self, worker_id: str, lease_seconds: int = 60) -> dict[str, Any] | None:
        lease = max(15, min(600, lease_seconds))
        lease_id = str(uuid.uuid4())
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id FROM agent_cloud_tasks
                   WHERE status='queued' OR (status='running' AND lease_expires_at<NOW())
                   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET status='running', execution_state='leased', worker_id=%s, lease_id=%s,
                       lease_expires_at=NOW()+(%s*INTERVAL '1 second'),
                       started_at=COALESCE(started_at,NOW()), attempts=attempts+1
                   WHERE id=%s RETURNING *""",
                (worker_id, lease_id, lease, row["id"]),
            )
            return dict(cursor.fetchone())

    def heartbeat_cloud(self, task_id: str, worker_id: str, lease_id: str, lease_seconds: int = 60) -> bool:
        lease = max(15, min(600, lease_seconds))
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET lease_expires_at=NOW()+(%s*INTERVAL '1 second')
                   WHERE id=%s AND status='running' AND worker_id=%s AND lease_id=%s
                     AND lease_expires_at>=NOW()""",
                (lease, task_id, worker_id, lease_id),
            )
            return cursor.rowcount == 1

    def update_cloud_state(self, task_id: str, worker_id: str, lease_id: str, state: str, proof: dict[str, Any] | None = None) -> bool:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE agent_cloud_tasks SET execution_state=%s, proof=COALESCE(%s,proof)
                   WHERE id=%s AND status='running' AND worker_id=%s AND lease_id=%s
                     AND lease_expires_at>=NOW()""",
                (state, Jsonb(proof) if proof is not None else None, task_id, worker_id, lease_id),
            )
            return cursor.rowcount == 1

    def finish_cloud(self, task_id: str, worker_id: str, lease_id: str, *, result: dict[str, Any] | None = None, error: str | None = None, proof: dict[str, Any] | None = None) -> bool:
        status = "failed" if error else "completed"
        final_state = "failed" if error else "completed"
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE agent_cloud_tasks
                   SET status=%s,execution_state=%s,result=%s,error=%s,proof=COALESCE(%s,proof),
                       completed_at=NOW(),lease_expires_at=NULL
                   WHERE id=%s AND status='running' AND worker_id=%s AND lease_id=%s
                     AND lease_expires_at>=NOW()""",
                (status, final_state, Jsonb(result or {}), error[:4000] if error else None, Jsonb(proof) if proof is not None else None, task_id, worker_id, lease_id),
            )
            return cursor.rowcount == 1

    def cancel_cloud(self, task_id: str) -> bool:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE agent_cloud_tasks SET execution_state='cancel_requested'
                   WHERE id=%s AND status IN ('queued','running')""",
                (task_id,),
            )
            return cursor.rowcount == 1

    def get_cloud(self, task_id: str) -> dict[str, Any] | None:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM agent_cloud_tasks WHERE id=%s", (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def record_route_observation(self, *, run_id: str, route: str, category: str, success: bool, incorrect_completion: bool, latency_ms: float, input_tokens: int = 0, output_tokens: int = 0, tool_failures: int = 0) -> bool:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO agent_route_observations
                   (run_id,route,category,success,incorrect_completion,latency_ms,input_tokens,output_tokens,tool_failures)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id) DO NOTHING""",
                (run_id, route, category, success, incorrect_completion, max(0.0, latency_ms), max(0, input_tokens), max(0, output_tokens), max(0, tool_failures)),
            )
            return cursor.rowcount == 1

    def route_scores(self, category: str) -> list[dict[str, Any]]:
        with self.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT route,COUNT(*) AS samples,
                          AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) AS success_rate,
                          AVG(CASE WHEN incorrect_completion THEN 1.0 ELSE 0.0 END) AS incorrect_rate,
                          AVG(latency_ms) AS latency_ms,
                          AVG(input_tokens+output_tokens) AS tokens,
                          AVG(tool_failures) AS tool_failures
                   FROM agent_route_observations WHERE category IN (%s,'general','*') GROUP BY route""",
                (category,),
            )
            rows = []
            for row in cursor.fetchall():
                item = dict(row)
                item["utility"] = (
                    float(item["success_rate"] or 0) * 100
                    - float(item["incorrect_rate"] or 0) * 80
                    - min(float(item["latency_ms"] or 0) / 1000, 30) * 0.25
                    - min(float(item["tokens"] or 0) / 10000, 20) * 0.5
                    - float(item["tool_failures"] or 0) * 3
                )
                rows.append(item)
            return sorted(rows, key=lambda item: (item["utility"], item["samples"]), reverse=True)
