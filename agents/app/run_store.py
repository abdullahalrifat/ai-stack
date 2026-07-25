"""Durable PostgreSQL storage for agent runs and their observable events."""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import POSTGRES_URL


class RunStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            yield connection

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
                    allow_write BOOLEAN NOT NULL DEFAULT FALSE,
                    sandbox_path TEXT,
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
            """)

    def create_run(
        self,
        *,
        task: str,
        model: str,
        workspace: str,
        conversation_id: str | None,
        allow_write: bool,
    ) -> str:
        run_id = str(uuid.uuid4())
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO agent_runs
                   (id, status, task, model, requested_workspace, conversation_id, allow_write)
                   VALUES (%s, 'queued', %s, %s, %s, %s, %s)""",
                (run_id, task, model, workspace, conversation_id, allow_write),
            )
        self.append_event(run_id, "queued", {"message": "Run queued"})
        return run_id

    def append_event(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO agent_run_events (run_id, event_type, payload) VALUES (%s, %s, %s)",
                (run_id, event_type, Jsonb(payload)),
            )

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

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
            return cursor.fetchone()

    def events_after(self, run_id: str, event_id: int = 0) -> list[dict[str, Any]]:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, event_type, payload, created_at FROM agent_run_events
                   WHERE run_id = %s AND id > %s ORDER BY id ASC""",
                (run_id, event_id),
            )
            return cursor.fetchall()


@lru_cache(maxsize=1)
def get_run_store() -> RunStore:
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL must be configured for durable agent runs")
    return RunStore(POSTGRES_URL)
