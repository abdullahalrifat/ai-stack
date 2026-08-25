from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.platform import router as platform_router
from app.platform import runtime
from app.platform.store import cron_matches, next_cron


def test_cron_matching_and_next_occurrence():
    value = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)
    assert cron_matches("0 8 * * 1-5", value)
    next_value = next_cron("*/15 * * * *", value)
    assert next_value == datetime(2026, 8, 24, 8, 15, tzinfo=timezone.utc)


def test_effective_route_uses_durable_route_event(monkeypatch):
    class Store:
        def events_after(self, run_id, event_id):
            assert run_id == "run-1"
            assert event_id == 0
            return [
                {"event_type": "queued", "payload": {}},
                {
                    "event_type": "route_selected",
                    "payload": {"model": "qwen3-coder", "workflow": "code"},
                },
            ]

    monkeypatch.setattr(runtime, "get_run_store", lambda: Store())
    assert runtime._effective_route("run-1", "orchestrator") == "qwen3-coder"


def test_incorrect_completion_uses_answer_audit(monkeypatch):
    class Store:
        def events_after(self, run_id, event_id):
            return [{"event_type": "answer_audit_failed", "payload": {}}]

    monkeypatch.setattr(runtime, "get_run_store", lambda: Store())
    assert runtime._incorrect_completion("run-2", "completed")
    assert not runtime._incorrect_completion("run-2", "failed")


def test_cloud_lease_rejects_wrong_worker(monkeypatch):
    lease_id = uuid4()

    class Store:
        def heartbeat_cloud(self, task_id, worker_id, request_lease_id, lease_seconds):
            assert request_lease_id == lease_id
            return worker_id == "owner"

        def finish_cloud(
            self,
            task_id,
            worker_id,
            request_lease_id,
            result=None,
            error=None,
            proof=None,
        ):
            assert request_lease_id == lease_id
            return worker_id == "owner"

    monkeypatch.setattr(platform_router, "PlatformStore", lambda: Store())
    heartbeat = platform_router.CloudHeartbeatRequest(
        worker_id="intruder", lease_id=lease_id, lease_seconds=30
    )
    with pytest.raises(HTTPException) as heartbeat_error:
        platform_router.heartbeat_cloud_task("task-1", heartbeat)
    assert heartbeat_error.value.status_code == 409

    completion = platform_router.CloudCompleteRequest(
        worker_id="intruder", lease_id=lease_id, result={"ok": True}
    )
    with pytest.raises(HTTPException) as completion_error:
        platform_router.complete_cloud_task("task-1", completion)
    assert completion_error.value.status_code == 409


def test_cloud_lease_owner_can_heartbeat_and_complete(monkeypatch):
    lease_id = uuid4()

    class Store:
        def heartbeat_cloud(self, task_id, worker_id, request_lease_id, lease_seconds):
            return (
                task_id == "task-1"
                and worker_id == "owner"
                and request_lease_id == lease_id
            )

        def finish_cloud(
            self,
            task_id,
            worker_id,
            request_lease_id,
            result=None,
            error=None,
            proof=None,
        ):
            return (
                task_id == "task-1"
                and worker_id == "owner"
                and request_lease_id == lease_id
                and result == {"ok": True}
            )

    monkeypatch.setattr(platform_router, "PlatformStore", lambda: Store())
    heartbeat = platform_router.CloudHeartbeatRequest(
        worker_id="owner", lease_id=lease_id, lease_seconds=30
    )
    assert platform_router.heartbeat_cloud_task("task-1", heartbeat) == {"ok": True}
    completion = platform_router.CloudCompleteRequest(
        worker_id="owner", lease_id=lease_id, result={"ok": True}
    )
    assert platform_router.complete_cloud_task("task-1", completion) == {"ok": True}
