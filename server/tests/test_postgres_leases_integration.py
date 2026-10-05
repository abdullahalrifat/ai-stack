"""Real PostgreSQL contention checks, skipped when no database is configured."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.runs.store import RunStore

POSTGRES_URL = os.getenv("POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="POSTGRES_URL is required for lease contention integration tests",
)


def _create_run(store: RunStore, client_id: str | None = None) -> str:
    return store.create_run(
        task="lease integration test",
        model="qwen3-4b",
        workspace="/workspace",
        conversation_id=str(uuid.uuid4()),
        document_scope=None,
        project_id=None,
        allow_write=False,
        client_id=client_id,
        client_lease_seconds=10,
    )


def _delete_runs(store: RunStore, run_ids: list[str]) -> None:
    with store.connection() as connection, connection.cursor() as cursor:
        for run_id in run_ids:
            cursor.execute("DELETE FROM agent_runs WHERE id = %s", (run_id,))


def test_concurrent_sweepers_claim_each_expired_row_once():
    store = RunStore(str(POSTGRES_URL))
    run_ids = [_create_run(store, f"terminal-{index}") for index in range(2)]
    try:
        with store.connection() as connection, connection.cursor() as cursor:
            for run_id in run_ids:
                cursor.execute(
                    """UPDATE agent_runs
                       SET client_lease_expires_at = NOW() - INTERVAL '1 second'
                       WHERE id = %s""",
                    (run_id,),
                )
        with ThreadPoolExecutor(max_workers=2) as executor:
            batches = list(
                executor.map(
                    lambda _index: store.cancel_expired_client_runs(limit=1),
                    range(2),
                )
            )

        claimed = [str(row["id"]) for batch in batches for row in batch]
        assert sorted(claimed) == sorted(run_ids)
        assert len(set(claimed)) == 2
    finally:
        _delete_runs(store, run_ids)
        store.close()


def test_concurrent_workers_cannot_claim_the_same_run():
    store = RunStore(str(POSTGRES_URL))
    run_id = _create_run(store)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda worker: store.claim_run(run_id, f"worker-{worker}"),
                    range(2),
                )
            )

        assert sorted(claims) == [False, True]
    finally:
        _delete_runs(store, [run_id])
        store.close()
