from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from app.api.protocol import FEATURES
from app.platform import router as platform_router
from app.platform.router import (
    CloudCompleteRequest,
    CloudHeartbeatRequest,
    CloudTaskRequest,
)
from app.platform.store import cron_matches


def test_v08_capabilities_are_advertised():
    for feature in (
        "cloud_lease_fencing",
        "cloud_idempotency_keys",
        "cloud_execution_states",
        "cloud_proof_ledger",
        "portable_cloud_model_profiles",
        "standard_cron_semantics",
    ):
        assert feature in FEATURES


def test_server_cron_uses_standard_dom_dow_or_and_sunday_seven():
    friday = datetime(2026, 1, 2, 8, 0, tzinfo=timezone.utc)
    first = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    sunday = datetime(2026, 1, 4, 8, 0, tzinfo=timezone.utc)
    assert cron_matches("0 8 1 * 5", friday)
    assert cron_matches("0 8 1 * 5", first)
    assert cron_matches("0 8 * * 7", sunday)


def test_cloud_requests_require_lease_fence_for_heartbeat_and_completion():
    heartbeat = CloudHeartbeatRequest(worker_id="worker", lease_id="lease", lease_seconds=60)
    complete = CloudCompleteRequest(worker_id="worker", lease_id="lease")
    assert heartbeat.lease_id == "lease"
    assert complete.lease_id == "lease"


def test_cloud_submission_accepts_idempotency_key():
    request = CloudTaskRequest(
        task="inspect",
        workspace="/workspace/repo",
        idempotency_key="request-12345678",
    )
    assert request.idempotency_key == "request-12345678"


def test_migration_adds_fencing_and_idempotency_columns():
    from pathlib import Path

    migration = Path("app/runs/migrations/009_autonomous_runtime.sql")
    if not migration.exists():
        migration = Path("server/app/runs/migrations/009_autonomous_runtime.sql")
    text = migration.read_text(encoding="utf-8")
    assert "lease_id UUID" in text
    assert "idempotency_key TEXT" in text
    assert "execution_state TEXT" in text
    assert "proof JSONB" in text
    assert "idx_agent_cloud_tasks_idempotency" in text
