"""Operational extensions for the autonomous engineering platform.

These services are deliberately adapters around the existing agent runtime:
model/provider changes never require changing the agent loop.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import urlparse

import requests

from app.core.config import DEFAULT_MODEL
from app.llm.client import get_available_models, get_llm_metrics


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def inference_status() -> dict[str, Any]:
    """Return health and model information from the dedicated inference VM."""
    base_url = os.getenv("INFERENCE_BASE_URL", "").rstrip("/")
    configured = bool(base_url)
    roles = {
        "coder": os.getenv("AGENT_REASONING_MODEL") or DEFAULT_MODEL,
        "reasoning": os.getenv("AGENT_REASONING_MODEL") or DEFAULT_MODEL,
        "vision": DEFAULT_MODEL,
    }
    try:
        available = list(get_available_models())
        gateway = {"status": "healthy", "models": available}
    except Exception as exc:
        gateway = {"status": "unavailable", "error": str(exc)[:500]}
        available = []
    return {
        "mode": "dedicated-inference",
        "primary_provider": "jarvis-inference",
        "fallback_provider": None,
        "remote_configured": False,
        "remote_base_url_configured": configured,
        "roles": {
            role: {"model": model, "provider": "jarvis-inference"}
            for role, model in roles.items()
        },
        "gateway": gateway,
        "models": available,
        "default_model": DEFAULT_MODEL,
        "web_search_enabled": _flag("WEB_SEARCH_ENABLED", True),
    }


def _github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for GitHub engineering operations")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _github_request(method: str, path: str, **kwargs: Any) -> Any:
    response = requests.request(method, f"https://api.github.com{path}", headers=_github_headers(), timeout=20, **kwargs)
    if not response.ok:
        raise RuntimeError(f"GitHub API {response.status_code}: {response.text[:1000]}")
    return response.json()


def bootstrap_issue(*, owner: str, repo: str, issue_number: int, branch: str, base: str | None = None, create_pr: bool = False, title: str | None = None, body: str | None = None) -> dict[str, Any]:
    issue = _github_request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}")
    if "pull_request" in issue:
        raise ValueError("issue_number points to a pull request, not an issue")
    repository = _github_request("GET", f"/repos/{owner}/{repo}")
    base_ref = base or repository.get("default_branch", "main")
    source = _github_request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{base_ref}")
    existing = None
    try:
        existing = _github_request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
    except RuntimeError as exc:
        if "404" not in str(exc):
            raise
    if existing is None:
        _github_request("POST", f"/repos/{owner}/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": source["object"]["sha"]})
    result: dict[str, Any] = {
        "issue": {"number": issue_number, "title": issue.get("title"), "url": issue.get("html_url")},
        "repository": f"{owner}/{repo}",
        "base": base_ref,
        "branch": branch,
        "branch_created": existing is None,
        "worktree_commands": [
            f"git fetch origin {branch}",
            f"git worktree add ../jarvis-{issue_number} {branch}",
        ],
    }
    if create_pr:
        pr = _github_request("POST", f"/repos/{owner}/{repo}/pulls", json={
            "title": title or f"{issue.get('title', 'Issue')} (#{issue_number})",
            "head": branch,
            "base": base_ref,
            "body": body or f"Closes #{issue_number}\n\nCreated by Jarvis engineering workflow.",
        })
        result["pull_request"] = {"number": pr["number"], "url": pr["html_url"], "state": pr["state"]}
    return result


def pull_request_review(*, owner: str, repo: str, number: int) -> dict[str, Any]:
    pr = _github_request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
    reviews = _github_request("GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews")
    comments = _github_request("GET", f"/repos/{owner}/{repo}/issues/{number}/comments")
    checks = _github_request("GET", f"/repos/{owner}/{repo}/commits/{pr['head']['sha']}/check-runs")
    return {
        "number": number,
        "state": pr.get("state"),
        "mergeable": pr.get("mergeable"),
        "draft": pr.get("draft"),
        "head": pr.get("head", {}).get("ref"),
        "base": pr.get("base", {}).get("ref"),
        "url": pr.get("html_url"),
        "checks": [{"name": item.get("name"), "status": item.get("status"), "conclusion": item.get("conclusion")} for item in checks.get("check_runs", [])],
        "reviews": [{"user": (item.get("user") or {}).get("login"), "state": item.get("state")} for item in reviews],
        "comments": len(comments),
    }


def identity_context(api_key: str, tenant: str | None, user: str | None) -> dict[str, Any]:
    tenancy = _flag("TENANCY_ENABLED", False)
    if tenancy and not tenant:
        raise ValueError("X-Jarvis-Tenant is required when TENANCY_ENABLED=true")
    tenant_value = (tenant or "single-user").strip()
    user_value = (user or "operator").strip()
    principal_digest = hashlib.sha256(f"{tenant_value}:{user_value}:{api_key}".encode()).hexdigest()
    return {"tenancy_enabled": tenancy, "tenant": tenant_value, "user": user_value, "principal_digest": principal_digest}


def backup_restore_check() -> dict[str, Any]:
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    configured = bool(os.getenv("POSTGRES_URL"))
    return {
        "postgres_configured": configured,
        "pg_dump_available": bool(pg_dump),
        "pg_restore_available": bool(pg_restore),
        "certified": bool(configured and pg_dump and pg_restore and os.getenv("BACKUP_RESTORE_CERTIFIED", "").lower() == "true"),
        "procedure": "pg_dump --format=custom followed by isolated pg_restore --exit-on-error and application smoke tests",
    }


def soak_profile() -> dict[str, Any]:
    return {
        "enabled": _flag("SOAK_ENABLED", False),
        "duration_hours": int(os.getenv("SOAK_DURATION_HOURS", "24")),
        "interval_seconds": int(os.getenv("SOAK_INTERVAL_SECONDS", "30")),
        "max_failures": int(os.getenv("SOAK_MAX_FAILURES", "3")),
        "worker_concurrency": int(os.getenv("MAX_CONCURRENT_AGENT_RUNS", "1")),
        "recommended": "run against disposable repositories and require zero lease-fence violations",
    }
