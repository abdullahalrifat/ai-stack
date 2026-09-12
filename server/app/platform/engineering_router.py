"""Authenticated P1/P2 engineering operations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import require_run_store, verify_api_key
from app.llm.client import get_llm_metrics
from .engineering import backup_restore_check, bootstrap_issue, identity_context, inference_status, pull_request_review, soak_profile

router = APIRouter(prefix="/engineering", tags=["engineering"], dependencies=[Depends(verify_api_key)])


class GitHubBootstrapRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    repo: str = Field(min_length=1, max_length=100)
    issue_number: int = Field(gt=0)
    branch: str = Field(min_length=1, max_length=255)
    base: str | None = None
    create_pr: bool = False
    title: str | None = None
    body: str | None = None


@router.get("/inference/status")
def inference():
    return inference_status()


@router.get("/llm/metrics")
def llm_metrics():
    return get_llm_metrics()


@router.post("/github/issue-bootstrap", dependencies=[Depends(require_run_store)])
def github_issue_bootstrap(request: GitHubBootstrapRequest):
    try:
        return bootstrap_issue(**request.model_dump())
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/github/pull/{owner}/{repo}/{number}")
def github_pull_review(owner: str, repo: str, number: int):
    try:
        return pull_request_review(owner=owner, repo=repo, number=number)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/backup-dr")
def backup_dr():
    return backup_restore_check()


@router.get("/soak")
def soak():
    return soak_profile()


@router.get("/identity")
def identity(x_jarvis_tenant: str | None = Header(None), x_jarvis_user: str | None = Header(None)):
    try:
        return identity_context("request-authenticated", x_jarvis_tenant, x_jarvis_user)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
