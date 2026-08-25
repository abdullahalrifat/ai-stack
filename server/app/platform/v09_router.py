"""v0.9 cloud admission API with quotas, worker capabilities and isolation specs."""

from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.dependencies import require_run_store, verify_api_key
from app.tools.filesystem import resolve_request_workspace

from .world_class_store import WorldClassPlatformStore

router = APIRouter(
    prefix="/platform/v09",
    tags=["platform-v09"],
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)


class ResourceLimits(BaseModel):
    cpu: float = Field(default=1.0, gt=0, le=32)
    memory_mb: int = Field(default=2048, ge=128, le=131072)
    pids: int = Field(default=256, ge=16, le=8192)
    disk_mb: int = Field(default=4096, ge=128, le=1048576)


class IsolationSpec(BaseModel):
    mode: Literal["trusted-host", "container", "microvm"] = "container"
    read_only_root: bool = True
    no_new_privileges: bool = True
    drop_capabilities: bool = True
    seccomp: bool = True


class EgressSpec(BaseModel):
    hosts: list[str] = Field(default_factory=list, max_length=128)
    allow_dns: bool = True

    @model_validator(mode="after")
    def validate_hosts(self):
        cleaned: list[str] = []
        for value in self.hosts:
            host = value.strip().casefold().rstrip(".")
            if not host or "/" in host or ":" in host or host.startswith("."):
                raise ValueError("egress hosts must be bare DNS names")
            cleaned.append(host)
        self.hosts = sorted(set(cleaned))
        return self


class WorldClassCloudTaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=100000)
    workspace: str | None = Field(default=None, max_length=4096)
    repository_url: str | None = Field(default=None, max_length=4096)
    git_ref: str | None = Field(default=None, max_length=255)
    git_commit: str | None = Field(default=None, max_length=64)
    model: str = Field(default="auto", min_length=1, max_length=200)
    allow_write: bool = False
    project_id: str | None = Field(default=None, max_length=256)
    tenant_id: str = Field(default="default", min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    isolation: IsolationSpec = Field(default_factory=IsolationSpec)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    egress: EgressSpec = Field(default_factory=EgressSpec)
    runtime: str = Field(default="auto", min_length=1, max_length=64)
    bootstrap: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self):
        self.task = self.task.strip()
        if bool(self.workspace) == bool(self.repository_url):
            raise ValueError("choose exactly one of workspace or repository_url")
        if self.repository_url:
            parsed = urlparse(self.repository_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("repository_url must be https")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("repository_url must not embed credentials/query/fragment")
        if self.git_commit:
            value = self.git_commit.strip().lower()
            if not 7 <= len(value) <= 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError("git_commit must be hexadecimal")
            self.git_commit = value
        if len(json.dumps(self.metadata, default=str).encode()) > 64 * 1024:
            raise ValueError("metadata exceeds 64 KiB")
        return self


class WorkerCapabilities(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=60, ge=15, le=600)
    container: bool = False
    microvm: bool = False
    egress_policy: bool = False
    max_cpu: float = Field(default=0, ge=0, le=128)
    max_memory_mb: int = Field(default=0, ge=0, le=1048576)
    runtimes: list[str] = Field(default_factory=lambda: ["auto"], max_length=64)


@router.post("/cloud/tasks")
def submit_task(request: WorldClassCloudTaskRequest):
    store = WorldClassPlatformStore()
    quota = max(1, min(int(__import__("os").getenv("JARVIS_TENANT_ACTIVE_TASK_LIMIT", "20")), 10000))
    if store.active_for_tenant(request.tenant_id) >= quota:
        raise HTTPException(429, "tenant active cloud-task quota reached")
    if request.isolation.mode == "trusted-host" and request.allow_write:
        if __import__("os").getenv("JARVIS_ALLOW_TRUSTED_HOST_WRITES", "false").casefold() not in {"1", "true", "yes"}:
            raise HTTPException(400, "write-capable cloud tasks require container/microvm isolation by default")
    workspace = None
    if request.repository_url:
        workspace_spec = {
            "kind": "git", "repository_url": request.repository_url,
            "git_ref": request.git_ref, "git_commit": request.git_commit,
        }
    else:
        try:
            workspace = resolve_request_workspace(str(request.workspace), request.task)
        except (PermissionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        workspace_spec = {"kind": "existing", "path": workspace}
    payload = {
        "task": request.task,
        "workspace": workspace,
        "workspace_spec": workspace_spec,
        "model": request.model,
        "allow_write": request.allow_write,
        "project_id": request.project_id,
        "tenant_id": request.tenant_id,
        "isolation": request.isolation.model_dump(),
        "resources": request.resources.model_dump(),
        "egress": request.egress.model_dump(),
        "runtime": request.runtime,
        "bootstrap": request.bootstrap,
        "metadata": request.metadata,
    }
    try:
        return store.submit_cloud(payload, idempotency_key=request.idempotency_key)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/cloud/claim")
def claim_task(request: WorkerCapabilities):
    capabilities = request.model_dump(exclude={"worker_id", "lease_seconds"})
    return {
        "task": WorldClassPlatformStore().claim_cloud(
            request.worker_id,
            request.lease_seconds,
            capabilities=capabilities,
        )
    }


@router.get("/capacity/{tenant_id}")
def tenant_capacity(tenant_id: str):
    active = WorldClassPlatformStore().active_for_tenant(tenant_id)
    limit = max(1, min(int(__import__("os").getenv("JARVIS_TENANT_ACTIVE_TASK_LIMIT", "20")), 10000))
    return {"tenant_id": tenant_id, "active": active, "limit": limit, "available": max(0, limit - active)}
