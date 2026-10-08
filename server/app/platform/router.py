"""Authenticated API for schedules, cloud-worker leases, and calibration."""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.dependencies import require_run_store, verify_api_key
from app.core.config import DEFAULT_MODEL
from app.tools.filesystem import resolve_request_workspace

from .autonomous_store import AutonomousPlatformStore as PlatformStore
from .telemetry import telemetry

router = APIRouter(
    prefix="/platform",
    tags=["platform"],
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)

_GIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_MAX_METADATA_BYTES = 64 * 1024
_MAX_PROOF_BYTES = 1536 * 1024
_MAX_RESULT_BYTES = 1536 * 1024


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _validate_git_ref(value: str | None) -> str | None:
    if value is None:
        return None
    ref = value.strip()
    if not ref:
        return None
    if (
        not _GIT_REF.fullmatch(ref)
        or ".." in ref
        or "@{" in ref
        or "//" in ref
        or ref.endswith(("/", "."))
        or ref.startswith("-")
        or any(part in {".", ".."} for part in ref.split("/"))
    ):
        raise ValueError("git_ref is not a safe Git branch/tag/ref name")
    return ref


class ScheduledRunRequest(BaseModel):
    name: str
    task: str
    workspace: str
    model: str = DEFAULT_MODEL
    allow_write: bool = False
    project_id: str | None = None
    conversation_id: str | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    cron: str | None = None


class CloudTaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=100_000)
    workspace: str | None = Field(default=None, max_length=4096)
    repository_url: str | None = Field(default=None, max_length=4096)
    git_ref: str | None = Field(default=None, max_length=255)
    git_commit: str | None = Field(default=None, max_length=64)
    model: str = Field(default="auto", min_length=1, max_length=200)
    allow_write: bool = False
    project_id: str | None = Field(default=None, max_length=256)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_workspace_source(self):
        self.task = self.task.strip()
        self.model = self.model.strip()
        if not self.task:
            raise ValueError("task is required")
        if not self.model:
            raise ValueError("model is required")
        if bool(self.workspace) == bool(self.repository_url):
            raise ValueError("choose exactly one of workspace or repository_url")
        if self.repository_url:
            parsed = urlparse(self.repository_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("cloud repository_url must be an https Git URL")
            if parsed.username or parsed.password:
                raise ValueError(
                    "repository_url must not embed credentials; configure worker Git credentials separately"
                )
            if parsed.query or parsed.fragment:
                raise ValueError("repository_url must not contain query or fragment data")
            self.git_ref = _validate_git_ref(self.git_ref)
        elif self.git_ref or self.git_commit:
            raise ValueError("git_ref/git_commit require repository_url")
        if self.git_commit:
            value = self.git_commit.strip().lower()
            if not (
                7 <= len(value) <= 64
                and all(ch in "0123456789abcdef" for ch in value)
            ):
                raise ValueError("git_commit must be a hexadecimal Git object id")
            self.git_commit = value
        if self.idempotency_key is not None:
            self.idempotency_key = self.idempotency_key.strip()
            if len(self.idempotency_key) < 8:
                raise ValueError("idempotency_key must contain at least 8 non-space characters")
        if _json_bytes(self.metadata) > _MAX_METADATA_BYTES:
            raise ValueError("cloud task metadata exceeds the 64 KiB safety limit")
        return self


class CloudClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=60, ge=15, le=600)


class CloudHeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    lease_id: UUID
    lease_seconds: int = Field(default=60, ge=15, le=600)


class CloudStateRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    lease_id: UUID
    state: Literal[
        "preparing_workspace",
        "running",
        "verifying",
        "uploading_result",
    ]
    proof: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_proof_size(self):
        if self.proof is not None and _json_bytes(self.proof) > _MAX_PROOF_BYTES:
            raise ValueError("cloud proof exceeds the 1.5 MiB safety limit")
        return self


class CloudCompleteRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    lease_id: UUID
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=4000)
    proof: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_result_sizes(self):
        if _json_bytes(self.result) > _MAX_RESULT_BYTES:
            raise ValueError("cloud result exceeds the 1.5 MiB safety limit")
        if self.proof is not None and _json_bytes(self.proof) > _MAX_PROOF_BYTES:
            raise ValueError("cloud proof exceeds the 1.5 MiB safety limit")
        return self


@router.get("/capabilities")
def platform_capabilities():
    """Describe wire protocols; this does not identify or require any client."""

    return {
        "service": "ai-stack",
        "protocols": {
            "agent": {"versions": [1], "event_schema_versions": [1]},
            "inference": {"versions": [1]},
            "cloud_execution": {
                "versions": [1],
                "proof_schema_versions": [1],
                "lease_fencing": True,
                "structured_completion_proof": True,
            }
        },
    }


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
    workspace: str | None = None
    workspace_spec: dict[str, Any]
    if request.repository_url:
        workspace_spec = {
            "kind": "git",
            "repository_url": request.repository_url,
            "git_ref": request.git_ref,
            "git_commit": request.git_commit,
        }
        workspace_label = "git:" + str(urlparse(request.repository_url).hostname)
    else:
        try:
            workspace = resolve_request_workspace(str(request.workspace), request.task)
        except (PermissionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        workspace_spec = {"kind": "existing", "path": workspace}
        workspace_label = workspace
    with telemetry.span(
        "jarvis.cloud.submit", workspace=workspace_label, model=request.model
    ):
        try:
            return PlatformStore().submit_cloud(
                {
                    "task": request.task,
                    "workspace": workspace,
                    "workspace_spec": workspace_spec,
                    "model": request.model,
                    "allow_write": request.allow_write,
                    "project_id": request.project_id,
                    "metadata": request.metadata,
                },
                idempotency_key=request.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/cloud/claim")
def claim_cloud_task(request: CloudClaimRequest):
    with telemetry.span("jarvis.cloud.claim", worker_id=request.worker_id):
        return {
            "task": PlatformStore().claim_cloud(
                request.worker_id, request.lease_seconds
            )
        }


@router.post("/cloud/tasks/{task_id}/heartbeat")
def heartbeat_cloud_task(task_id: UUID, request: CloudHeartbeatRequest):
    if not PlatformStore().heartbeat_cloud(
        task_id,
        request.worker_id,
        request.lease_id,
        request.lease_seconds,
    ):
        raise HTTPException(409, "cloud task lease fence is stale or not owned")
    return {"ok": True}


@router.post("/cloud/tasks/{task_id}/state")
def update_cloud_task_state(task_id: UUID, request: CloudStateRequest):
    try:
        updated = PlatformStore().update_cloud_state(
            task_id,
            request.worker_id,
            request.lease_id,
            request.state,
            request.proof,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not updated:
        raise HTTPException(409, "cloud task lease fence is stale or not owned")
    return {"ok": True}


@router.post("/cloud/tasks/{task_id}/complete")
def complete_cloud_task(task_id: UUID, request: CloudCompleteRequest):
    if not PlatformStore().finish_cloud(
        task_id,
        request.worker_id,
        request.lease_id,
        result=request.result,
        error=request.error,
        proof=request.proof,
    ):
        raise HTTPException(409, "cloud task lease fence is stale or not owned")
    return {"ok": True}


@router.post("/cloud/tasks/{task_id}/cancel")
def cancel_cloud_task(task_id: UUID):
    if not PlatformStore().cancel_cloud(task_id):
        raise HTTPException(404, "cloud task not found or already terminal")
    return {"ok": True}


@router.get("/cloud/tasks/{task_id}")
def get_cloud_task(task_id: UUID):
    task = PlatformStore().get_cloud(task_id)
    if task is None:
        raise HTTPException(404, "cloud task not found")
    return task


@router.get("/calibration")
def calibration(category: str = "code"):
    return {
        "category": category,
        "routes": PlatformStore().route_scores(category),
    }
