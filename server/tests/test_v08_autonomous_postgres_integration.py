"""PostgreSQL integration tests for v0.8 autonomous cloud fencing."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.platform.autonomous_store import AutonomousPlatformStore

POSTGRES_URL = os.getenv("POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="POSTGRES_URL is required for autonomous cloud integration tests",
)


def _store() -> AutonomousPlatformStore:
    store = AutonomousPlatformStore()
    store.runs.initialize()
    return store


def _delete(store: AutonomousPlatformStore, task_ids: set[str]) -> None:
    if not task_ids:
        return
    with store.runs.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM agent_cloud_tasks WHERE id = ANY(%s::uuid[])",
            (list(task_ids),),
        )


def _proof(task_id: str, lease_id: str, attempt: int = 1):
    return {
        "schema_version": 1,
        "task_id": task_id,
        "lease_id": lease_id,
        "attempt": attempt,
        "workspace_digest": "a" * 64,
        "route": "coding",
        "model": "test-model",
        "mutation_digest": "b" * 64,
        "verifications": [
            {
                "command": "pytest -q",
                "status": "passed",
                "exit_code": 0,
                "output_digest": "c" * 64,
            }
        ],
        "artifact_hashes": {},
    }


def test_concurrent_idempotent_submission_creates_one_task():
    store = _store()
    task_ids: set[str] = set()
    key = f"integration-idempotency-v08-{uuid.uuid4()}"
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            rows = list(
                executor.map(
                    lambda _index: store.submit_cloud(
                        {"task": "same task", "model": "auto"},
                        idempotency_key=key,
                    ),
                    range(4),
                )
            )
        task_ids = {str(row["id"]) for row in rows}
        assert len(task_ids) == 1
    finally:
        _delete(store, task_ids)


def test_idempotency_key_rejects_different_payload():
    store = _store()
    key = f"integration-idempotency-mismatch-v08-{uuid.uuid4()}"
    created = store.submit_cloud(
        {"task": "first task", "model": "auto"},
        idempotency_key=key,
    )
    task_id = str(created["id"])
    try:
        repeated = store.submit_cloud(
            {"task": "first task", "model": "auto"},
            idempotency_key=key,
        )
        assert str(repeated["id"]) == task_id
        with pytest.raises(ValueError, match="different cloud task payload"):
            store.submit_cloud(
                {"task": "different task", "model": "auto"},
                idempotency_key=key,
            )
    finally:
        _delete(store, {task_id})


def test_lease_fence_rejects_wrong_worker_and_stale_attempt():
    store = _store()
    created = store.submit_cloud({"task": "fenced task", "model": "auto"})
    task_id = str(created["id"])
    try:
        first = store.claim_cloud("worker-a", 30)
        assert first is not None
        lease_a = str(first["lease_id"])
        assert store.heartbeat_cloud(task_id, "worker-a", lease_a, 30)
        assert not store.heartbeat_cloud(task_id, "worker-b", lease_a, 30)
        assert not store.heartbeat_cloud(
            task_id,
            "worker-a",
            "00000000-0000-0000-0000-000000000000",
            30,
        )

        with store.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE agent_cloud_tasks SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s",
                (task_id,),
            )

        second = store.claim_cloud("worker-b", 30)
        assert second is not None
        assert str(second["id"]) == task_id
        lease_b = str(second["lease_id"])
        assert lease_b != lease_a
        assert not store.heartbeat_cloud(task_id, "worker-a", lease_a, 30)
        assert not store.finish_cloud(
            task_id,
            "worker-a",
            lease_a,
            result={"stale": True},
        )
        assert store.heartbeat_cloud(task_id, "worker-b", lease_b, 30)
    finally:
        _delete(store, {task_id})


def test_successful_completion_requires_upload_state_and_proof():
    store = _store()
    created = store.submit_cloud({"task": "proof-gated task", "model": "auto"})
    task_id = str(created["id"])
    try:
        claimed = store.claim_cloud("worker-proof", 30)
        assert claimed is not None
        lease_id = str(claimed["lease_id"])

        # A fenced worker cannot skip preparation, execution, verification,
        # and result-upload states even when it submits a proof object.
        assert not store.finish_cloud(
            task_id,
            "worker-proof",
            lease_id,
            result={"premature": True},
            proof=_proof(task_id, lease_id),
        )

        for state in (
            "preparing_workspace",
            "running",
            "verifying",
            "uploading_result",
        ):
            assert store.update_cloud_state(
                task_id,
                "worker-proof",
                lease_id,
                state,
            )

        # Reaching the final worker state is insufficient without durable proof.
        assert not store.finish_cloud(
            task_id,
            "worker-proof",
            lease_id,
            result={"missing_proof": True},
        )
        assert store.finish_cloud(
            task_id,
            "worker-proof",
            lease_id,
            result={"ok": True},
            proof=_proof(task_id, lease_id),
        )
    finally:
        _delete(store, {task_id})


def test_cloud_state_machine_and_fenced_completion():
    store = _store()
    created = store.submit_cloud({"task": "state task", "model": "auto"})
    task_id = str(created["id"])
    try:
        claimed = store.claim_cloud("worker-state", 30)
        assert claimed is not None
        assert str(claimed["id"]) == task_id
        lease_id = str(claimed["lease_id"])
        for state in (
            "preparing_workspace",
            "running",
            "verifying",
            "uploading_result",
        ):
            assert store.update_cloud_state(
                task_id,
                "worker-state",
                lease_id,
                state,
                {"state": state},
            )
        assert store.finish_cloud(
            task_id,
            "worker-state",
            lease_id,
            result={"ok": True},
            proof={"verified": True},
        )
        row = store.get_cloud(task_id)
        assert row is not None
        assert row["status"] == "completed"
        assert row["execution_state"] == "completed"
        assert row["lease_id"] is None
    finally:
        _delete(store, {task_id})


def test_cancelled_task_is_terminal_and_never_reclaimed():
    store = _store()
    created = store.submit_cloud({"task": "cancel task", "model": "auto"})
    task_id = str(created["id"])
    try:
        claimed = store.claim_cloud("worker-cancel", 30)
        assert claimed is not None
        assert str(claimed["id"]) == task_id
        lease_id = str(claimed["lease_id"])
        assert store.cancel_cloud(task_id)
        assert not store.heartbeat_cloud(task_id, "worker-cancel", lease_id, 30)
        assert not store.finish_cloud(
            task_id,
            "worker-cancel",
            lease_id,
            result={"should_not_publish": True},
        )
        row = store.get_cloud(task_id)
        assert row is not None
        assert row["status"] == "cancelled"
        assert row["execution_state"] == "cancelled"
        assert row["lease_id"] is None
        with store.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM agent_cloud_tasks "
                "WHERE id=%s AND (status='queued' OR status='running')",
                (task_id,),
            )
            assert int(cursor.fetchone()["count"]) == 0
    finally:
        _delete(store, {task_id})


def test_expired_lease_cannot_complete_without_reclaim():
    store = _store()
    created = store.submit_cloud({"task": "expiry task", "model": "auto"})
    task_id = str(created["id"])
    try:
        claimed = store.claim_cloud("worker-expired", 30)
        assert claimed is not None
        assert str(claimed["id"]) == task_id
        lease_id = str(claimed["lease_id"])
        with store.runs.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE agent_cloud_tasks SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s",
                (task_id,),
            )
        assert not store.finish_cloud(
            task_id,
            "worker-expired",
            lease_id,
            result={"too_late": True},
        )
    finally:
        _delete(store, {task_id})
