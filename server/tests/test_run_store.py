from contextlib import contextmanager
from unittest.mock import MagicMock

from app.runs.store import MIGRATIONS_DIR, RUN_WORKER_LEASE_SECONDS, RunStore


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


def test_append_event_escapes_postgres_incompatible_text():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 7, "created_at": "now"}
    store = store_with_cursor(cursor)

    event = store.append_event(
        "run-1",
        "tool_result",
        {"content": "binary\x00data", "nested": ["bad\ud800text"]},
    )

    stored = cursor.execute.call_args.args[1][2].obj
    assert stored == {
        "content": "binary\\x00data",
        "nested": ["bad\\ud800text"],
    }
    assert event["payload"] == stored


def test_initialize_applies_versioned_migrations_once():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    store = store_with_cursor(cursor)

    store.initialize()

    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("CREATE TABLE IF NOT EXISTS agent_runs" in query for query in executed)
    assert cursor.execute.call_args_list[-1].args == (
        "INSERT INTO agent_schema_migrations (version) VALUES (%s)",
        ("003",),
    )
    assert (MIGRATIONS_DIR / "001_initial.sql").is_file()
    assert (MIGRATIONS_DIR / "002_client_leases.sql").is_file()
    assert (MIGRATIONS_DIR / "003_client_lease_sweeping.sql").is_file()


def test_heartbeat_requires_the_current_worker():
    cursor = MagicMock()
    cursor.rowcount = 0
    store = store_with_cursor(cursor)

    assert store.heartbeat_run("run-1", "other-worker") is False

    query, params = cursor.execute.call_args.args
    assert "worker_id = %s" in query
    assert params == (RUN_WORKER_LEASE_SECONDS, "run-1", "other-worker")


def test_client_lease_renewal_requires_matching_foreground_client():
    cursor = MagicMock()
    cursor.rowcount = 1
    store = store_with_cursor(cursor)

    assert store.renew_client_lease("run-1", "terminal-1", lease_seconds=2) is True

    query, params = cursor.execute.call_args.args
    assert "client_id = %s" in query
    assert "client_lease_renewed_at = NOW()" in query
    assert "status IN ('queued', 'running')" in query
    assert params == (10, "run-1", "terminal-1")


def test_initial_client_lease_uses_database_clock():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 1, "created_at": "now"}
    store = store_with_cursor(cursor)

    store.create_run(
        task="inspect",
        model="orchestrator",
        workspace="/workspace/repo",
        conversation_id="conversation",
        document_scope=None,
        project_id=None,
        allow_write=False,
        client_id="terminal-1",
        client_lease_seconds=30,
    )

    query, params = cursor.execute.call_args_list[0].args
    assert "client_lease_renewed_at" in query
    assert query.count("%s::text IS NULL") == 2
    assert "NOW() + (%s * INTERVAL '1 second')" in query
    assert params[-3:] == ("terminal-1", 30, "terminal-1")


def test_client_lease_health_uses_database_clock():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "active_foreground_runs": 2,
        "expired_foreground_runs": 1,
        "maximum_renewal_age_seconds": 12,
    }
    store = store_with_cursor(cursor)

    health = store.client_lease_health()

    assert health["expired_foreground_runs"] == 1
    query = cursor.execute.call_args.args[0]
    assert "NOW() - client_lease_renewed_at" in query


def test_expired_foreground_leases_request_cancellation_atomically():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"id": "run-1", "status": "cancelling"},
        {"id": "run-2", "status": "cancelled"},
    ]
    store = store_with_cursor(cursor)

    assert store.cancel_expired_client_runs() == [
        {"id": "run-1", "status": "cancelling"},
        {"id": "run-2", "status": "cancelled"},
    ]

    query = cursor.execute.call_args.args[0]
    assert "client_lease_expires_at < NOW()" in query
    assert "FOR UPDATE SKIP LOCKED" in query
    assert "WHEN runs.status = 'queued' THEN 'cancelled'" in query
    assert "RETURNING runs.id, runs.status" in query
    assert cursor.execute.call_args.args[1] == (100,)


def test_worker_observes_an_expired_foreground_lease_as_cancellation():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"cancelling": True}
    store = store_with_cursor(cursor)

    assert store.is_cancel_requested("run-1") is True

    expiry_query = cursor.execute.call_args_list[0].args[0]
    assert "client_lease_expires_at < NOW()" in expiry_query
    assert cursor.execute.call_args_list[1].args[1] == ("run-1",)


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
    recovery_queries = [call.args[0] for call in cursor.execute.call_args_list[1:]]
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


def test_terminal_sandbox_reconciliation_is_bounded_and_repeatable():
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "id": "run-1",
            "repository_path": "/repo",
            "sandbox_path": "/sandbox",
        }
    ]
    store = store_with_cursor(cursor)

    assert store.sandboxes_needing_cleanup(limit=5)[0]["id"] == "run-1"

    query, params = cursor.execute.call_args.args
    assert "status IN ('failed', 'cancelled', 'discarded', 'completed')" in query
    assert "sandbox_path IS NOT NULL" in query
    assert params == (5,)
