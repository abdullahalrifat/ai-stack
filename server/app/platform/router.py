"""Authenticated API for schedules, cloud-worker leases, and calibration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import require_run_store, verify_api_key
from app.tools.filesystem import resolve_request_workspace

from .store import PlatformStore
from .telemetry import telemetry

router = APIRouter(
    prefix="/platform",
    tags=["platform"],
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)


class ScheduledRunRequest(BaseModel):
    name: str
    task: str
    workspace: str
    model: str = "orchestrator"
    allow_write: bool = False
    project_id: str | None = None
    conversation_id: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    cron: str | None = None


class CloudTaskRequest(BaseModel):
    task: str
    workspace: str
    model: str = "auto"
    allow_write: bool = False
    project_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CloudClaimRequest(BaseModel):
    worker_id: str
    lease_seconds: int = Field(default=60, ge=15, le=600)


class CloudHeartbeatRequest(BaseModel):
    worker_id: str
    lease_seconds: int = Field(default=60, ge=15, le=600)


class CloudCompleteRequest(BaseModel):
    worker_id: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


@router.post("/schedules")
def create_schedule(request: ScheduledRunRequest):
    if not request.task.strip() or not request.name.strip():
        raise HTTPException(400, "schedule name and task are required")
    try:
        workspace = resolve_request_workspace(request.workspace, request.task)
        with telemetry.span("jarvis.platform.schedule.create", name=request.name):
            return PlatformStore().create_schedule(
                name=request.name,
                payload={
                    "task": request.task,
                    "workspace": workspace,
                    "model": request.model,
                    "allow_write": request.allow_write,
                    "project_id": request.project_id,
                    "conversation_id": request.conversation_id,
                },
                interval_seconds=request.interval_seconds,
                cron=request.cron,
            )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/schedules")
def list_schedules():
    return {"schedules": PlatformStore().list_schedules()}


@router.post("/schedules/{schedule_id}/enable")
def enable_schedule(schedule_id: str):
    if not PlatformStore().set_schedule_enabled(schedule_id, True):
        raise HTTPException(404, "schedule not found")
    return {"id": schedule_id, "enabled": True}


@router.post("/schedules/{schedule_id}/disable")
def disable_schedule(schedule_id: str):
    if not PlatformStore().set_schedule_enabled(schedule_id, False):
        raise HTTPException(404, "schedule not found")
    return {"id": schedule_id, "enabled": False}


@router.post("/cloud/tasks")
def submit_cloud_task(request: CloudTaskRequest):
    if not request.task.strip():
        raise HTTPException(400, "task is required")
    try:
        workspace = resolve_request_workspace(request.workspace, request.task)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with telemetry.span("jarvis.cloud.submit", workspace=workspace, model=request.model):
        return PlatformStore().submit_cloud(
            {
                "task": request.task,
                "workspace": workspace,
                "model": request.model,
                "allow_write": request.allow_write,
                "project_id": request.project_id,
                "metadata": request.metadata,
            }
        )


@router.post("/cloud/claim")
def claim_cloud_task(request: CloudClaimRequest):
    with telemetry.span("jarvis.cloud.claim", worker_id=request.worker_id):
        return {"task": PlatformStore().claim_cloud(request.worker_id, request.lease_seconds)}


@router.post("/cloud/tasks/{task_id}/heartbeat")
def heartbeat_cloud_task(task_id: str, request: CloudHeartbeatRequest):
    if not PlatformStore().heartbeat_cloud(task_id, request.worker_id, request.lease_seconds):
        raise HTTPException(409, "cloud task lease is not owned by this worker")
    return {"ok": True}


@router.post("/cloud/tasks/{task_id}/complete")
def complete_cloud_task(task_id: str, request: CloudCompleteRequest):
    if not PlatformStore().finish_cloud(task_id, request.worker_id, result=request.result, error=request.error):
        raise HTTPException(409, "cloud task lease is not owned by this worker")
    return {"ok": True}


@router.get("/cloud/tasks/{task_id}")
def get_cloud_task(task_id: str):
    task = PlatformStore().get_cloud(task_id)
    if task is None:
        raise HTTPException(404, "cloud task not found")
    return task


@router.get("/calibration")
def calibration(category: str = "code"):
    return {"category": category, "routes": PlatformStore().route_scores(category)}
