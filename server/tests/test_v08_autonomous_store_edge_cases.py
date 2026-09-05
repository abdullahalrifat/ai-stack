"""Additional PostgreSQL coverage for autonomous cloud store edge cases."""

from __future__ import annotations

import os
import uuid

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


def _proof(task_id: str, lease_id: str, attempt: int = 1) -> dict:
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


def _delete(store: AutonomousPlatformStore, task_id: str) -> None:
    with store.runs.connection() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM agent_cloud_tasks WHERE id=%s", (task_id,))


def test_invalid_execution_state_is_rejected():
    store = _store()
    created = store.submit_cloud({"task": "invalid state", "model": "auto"})
    task_id = str(created["id"])
    try:
        claimed = store.claim_cloud("worker-invalid", 30)
        assert claimed is not None
        lease_id = str(claimed["lease_id"])
        with pytest.raises(ValueError, match="unsupported worker execution state"):
            store.update_cloud_state(task_id, "worker-invalid", lease_id, "not-a-state")
    finally:
        _delete(store, task_id)


def test_invalid_state_transition_is_rejected():
    store = _store()
    created = store.submit_cloud({"task": "invalid transition", "model": "auto"})
    task_id = str(created["id"])
    try:
        claimed = store.claim_cloud("worker-transition", 30)
        assert claimed is not None
        lease_id = str(claimed["lease_id"])
        with pytest.raises(ValueError, match="invalid cloud execution transition"):
            store.update_cloud_state(task_id, "worker-transition", lease_id, "running")
    finally:
        _delete(store, task_id)


def test_invalid_success_proof_is_rejected():
    store = _store()
    created = store.submit_cloud({"task": "invalid proof", "model": "auto"})
    task_id = str(created["id"])
    try:
        claimed = store.claim_cloud("worker-proof-invalid", 30)
        assert claimed is not None
        lease_id = str(claimed["lease_id"])
        for state in (
            "preparing_workspace",
            "running",
            "verifying",
            "uploading_result",
        ):
            assert store.update_cloud_state(
                task_id, "worker-proof-invalid", lease_id, state
            )
        invalid = _proof(task_id, str(uuid.uuid4()))
        assert not store.finish_cloud(
            task_id,
            "worker-proof-invalid",
            lease_id,
            result={"ok": True},
            proof=invalid,
        )
    finally:
        _delete(store, task_id)


def test_failed_completion_is_allowed_without_execution_proof():
    store = _store()
    created = store.submit_cloud({"task": "failed completion", "model": "auto"})
    task_id = str(created["id"])
    try:
        claimed = store.claim_cloud("worker-failed", 30)
        assert claimed is not None
        lease_id = str(claimed["lease_id"])
        assert store.finish_cloud(
            task_id,
            "worker-failed",
            lease_id,
            error="worker execution failed",
        )
        row = store.get_cloud(task_id)
        assert row is not None
        assert row["status"] == "failed"
        assert row["execution_state"] == "failed"
    finally:
        _delete(store, task_id)


def test_cancel_queued_task_is_terminal():
    store = _store()
    created = store.submit_cloud({"task": "queued cancel", "model": "auto"})
    task_id = str(created["id"])
    try:
        assert store.cancel_cloud(task_id)
        assert store.claim_cloud("worker-after-cancel", 30) is None
        row = store.get_cloud(task_id)
        assert row is not None
        assert row["status"] == "cancelled"
        assert row["execution_state"] == "cancelled"
    finally:
        _delete(store, task_id)
