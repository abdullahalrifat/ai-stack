"""Durable PostgreSQL storage for agent runs and their observable events."""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ..core.config import POSTGRES_URL


class RunStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=5,
            open=True,
            kwargs={"row_factory": dict_row},
        )

    @contextmanager
    def connection(self):
        with self.pool.connection() as connection:
            yield connection

    def close(self) -> None:
        self.pool.close()

    def initialize(self) -> None:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id UUID PRIMARY KEY,
                    status TEXT NOT NULL,
                    task TEXT NOT NULL,
                    model TEXT NOT NULL,
                    requested_workspace TEXT NOT NULL,
                    active_workspace TEXT,
                    conversation_id TEXT,
                    document_scope TEXT,
                    project_id UUID,
                    allow_write BOOLEAN NOT NULL DEFAULT FALSE,
                    sandbox_path TEXT,
                    repository_path TEXT,
                    base_commit TEXT,
                    checkpoint JSONB,
                    answer TEXT,
                    error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS agent_run_events (
                    id BIGSERIAL PRIMARY KEY,
                    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS agent_run_events_run_id_id_idx
                    ON agent_run_events (run_id, id);
                CREATE TABLE IF NOT EXISTS agent_projects (
                    id UUID PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    workspace TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS repository_path TEXT;
                ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS base_commit TEXT;
                ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS document_scope TEXT;
                ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS checkpoint JSONB;
                ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS project_id UUID;
            """)

    def create_run(
        self,
        *,
        task: str,
        model: str,
        workspace: str,
        conversation_id: str | None,
        document_scope: str | None,
        project_id: str | None,
        allow_write: bool,
    ) -> str:
        run_id = str(uuid.uuid4())
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO agent_runs
                   (id, status, task, model, requested_workspace, conversation_id, document_scope, project_id, allow_write)
                   VALUES (%s, 'queued', %s, %s, %s, %s, %s, %s, %s)""",
                (run_id, task, model, workspace, conversation_id, document_scope, project_id, allow_write),
            )
        self.append_event(run_id, "queued", {"message": "Run queued"})
        return run_id

    def append_event(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO agent_run_events (run_id, event_type, payload)
                   VALUES (%s, %s, %s) RETURNING id, created_at""",
                (run_id, event_type, Jsonb(payload)),
            )
            row = cursor.fetchone()
        return {
            "id": row["id"],
            "run_id": run_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": row["created_at"],
        }

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = datetime.now(timezone.utc)
        assignments = ", ".join(f"{key} = %s" for key in fields)
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE agent_runs SET {assignments} WHERE id = %s",
                [*fields.values(), run_id],
            )

    def update_checkpoint(self, run_id: str, checkpoint: dict[str, Any]) -> None:
        self.update_run(run_id, checkpoint=Jsonb(checkpoint))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
            return cursor.fetchone()

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, status, left(task, 240) AS task, model, requested_workspace, conversation_id, project_id,
                          allow_write, error, created_at, completed_at
                   FROM agent_runs ORDER BY created_at DESC LIMIT %s""",
                (min(max(limit, 1), 200),),
            )
            return cursor.fetchall()

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id, name, workspace, created_at, updated_at FROM agent_projects ORDER BY updated_at DESC")
            return cursor.fetchall()

    def create_project(self, name: str, workspace: str) -> dict[str, Any]:
        project_id = str(uuid.uuid4())
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO agent_projects (id, name, workspace) VALUES (%s, %s, %s)
                   RETURNING id, name, workspace, created_at, updated_at""",
                (project_id, name.strip(), workspace),
            )
            return cursor.fetchone()

    def events_after(self, run_id: str, event_id: int = 0) -> list[dict[str, Any]]:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, event_type, payload, created_at FROM agent_run_events
                   WHERE run_id = %s AND id > %s ORDER BY id ASC""",
                (run_id, event_id),
            )
            return cursor.fetchall()

    def request_cancel(self, run_id: str) -> bool:
        """Mark a queued/running run for cooperative cancellation."""
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE agent_runs SET status = 'cancelling', updated_at = NOW()
                   WHERE id = %s AND status IN ('queued', 'running')""",
                (run_id,),
            )
            changed = cursor.rowcount == 1
        return changed

    def is_cancel_requested(self, run_id: str) -> bool:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT status = 'cancelling' AS cancelling FROM agent_runs WHERE id = %s", (run_id,))
            row = cursor.fetchone()
            return bool(row and row["cancelling"])

    def recover_interrupted_runs(self) -> tuple[list[str], list[dict[str, Any]]]:
        """Resume checkpointed read-only work; clean up interrupted writes."""
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id FROM agent_runs WHERE status = 'queued' ORDER BY created_at")
            queued = [str(row["id"]) for row in cursor.fetchall()]
            cursor.execute(
                """UPDATE agent_runs SET status = 'queued', error = NULL, updated_at = NOW()
                   WHERE status = 'running' AND allow_write = FALSE
                   RETURNING id"""
            )
            resumed = [str(row["id"]) for row in cursor.fetchall()]
            queued.extend(run_id for run_id in resumed if run_id not in queued)
            cursor.execute(
                """UPDATE agent_runs SET status = 'failed', error = 'Agent service restarted while run was active',
                       completed_at = NOW(), updated_at = NOW()
                   WHERE (status = 'running' AND allow_write = TRUE) OR status = 'cancelling'
                   RETURNING id, repository_path, sandbox_path"""
            )
            interrupted = list(cursor.fetchall())
        for run_id in resumed:
            self.append_event(run_id, "run_resuming", {"reason": "service_restart"})
        for run in interrupted:
            self.append_event(str(run["id"]), "run_interrupted", {"reason": "service_restart"})
        return queued, interrupted


@lru_cache(maxsize=1)
def get_run_store() -> RunStore:
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL must be configured for durable agent runs")
    return RunStore(POSTGRES_URL)
