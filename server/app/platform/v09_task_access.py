"""Tenant-scoped status/cancel access for v0.9 cloud tasks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID

from app.api.dependencies import require_run_store

from .auth_v09 import authenticate_v09, enforce_tenant
from .world_class_store import WorldClassPlatformStore

router = APIRouter(
    prefix="/platform/v09",
    tags=["platform-v09"],
    dependencies=[Depends(require_run_store)],
)


def _task_for_access(task_id: UUID, authenticated_tenant: str):
    task = WorldClassPlatformStore().get_cloud(task_id)
    if task is None:
        raise HTTPException(404, "cloud task not found")
    payload = dict(task.get("payload") or {})
    tenant = str(payload.get("tenant_id") or "default")
    enforce_tenant(authenticated_tenant, tenant)
    return task


@router.get("/cloud/tasks/{task_id}")
def get_v09_cloud_task(
    task_id: UUID,
    authenticated_tenant: str = Depends(authenticate_v09),
):
    return _task_for_access(task_id, authenticated_tenant)


@router.post("/cloud/tasks/{task_id}/cancel")
def cancel_v09_cloud_task(
    task_id: UUID,
    authenticated_tenant: str = Depends(authenticate_v09),
):
    _task_for_access(task_id, authenticated_tenant)
    if not WorldClassPlatformStore().cancel_cloud(task_id):
        raise HTTPException(404, "cloud task not found or already terminal")
    return {"ok": True}
