from contextlib import contextmanager
from unittest.mock import MagicMock

from app.runs.store import MIGRATIONS_DIR, RunStore


def store_with_cursor(cursor):
    store = RunStore.__new__(RunStore)
    cursor.__enter__.return_value = cursor
    connection = MagicMock()
    connection.cursor.return_value = cursor

    @contextmanager
    def fake_connection():
        yield connection

    store.connection = fake_connection
    return store


def test_claim_run_is_atomic_and_enforces_minimum_lease():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": "run-1"}
    store = store_with_cursor(cursor)

    assert store.claim_run("run-1", "worker-1", lease_seconds=2) is True

    query, params = cursor.execute.call_args.args
    assert "status = 'queued'" in query
    assert "lease_expires_at < NOW()" in query
    assert params == ("worker-1", 30, "run-1")


def test_initialize_applies_versioned_migrations_once():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    store = store_with_cursor(cursor)

    store.initialize()

    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("CREATE TABLE IF NOT EXISTS agent_runs" in query for query in executed)
    assert cursor.execute.call_args_list[-1].args == (
        "INSERT INTO agent_schema_migrations (version) VALUES (%s)",
        ("001",),
    )
    assert (MIGRATIONS_DIR / "001_initial.sql").is_file()


def test_heartbeat_requires_the_current_worker():
    cursor = MagicMock()
    cursor.rowcount = 0
    store = store_with_cursor(cursor)

    assert store.heartbeat_run("run-1", "other-worker") is False

    query, params = cursor.execute.call_args.args
    assert "worker_id = %s" in query
    assert params == (600, "run-1", "other-worker")


def test_recovery_only_takes_expired_or_legacy_leases():
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [{"id": "queued"}],
        [{"id": "read-only"}],
        [
            {
                "id": "write-run",
                "repository_path": "/repo",
                "sandbox_path": "/sandbox",
            }
        ],
    ]
    store = store_with_cursor(cursor)
    store.append_event = MagicMock()

    queued, interrupted = store.recover_interrupted_runs()

    assert queued == ["queued", "read-only"]
    assert interrupted[0]["id"] == "write-run"
    recovery_queries = [
        call.args[0] for call in cursor.execute.call_args_list[1:]
    ]
    assert all(
        "lease_expires_at IS NULL OR lease_expires_at < NOW()" in query
        for query in recovery_queries
    )
    assert store.append_event.call_count == 2


def test_queued_cancellation_is_terminal_without_a_worker():
    cursor = MagicMock()
    cursor.rowcount = 1
    store = store_with_cursor(cursor)

    assert store.request_cancel("run-1") is True

    query, params = cursor.execute.call_args.args
    assert "WHEN status = 'queued' THEN 'cancelled'" in query
    assert "WHEN status = 'queued' THEN NOW()" in query
    assert params == ("run-1",)
