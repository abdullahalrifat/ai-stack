"""Minimal GitHub PR/issue integration with explicit write gating."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GitHubIntegrationError(RuntimeError):
    pass


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise GitHubIntegrationError("GITHUB_TOKEN is not configured")
    if not path.startswith("/repos/"):
        raise GitHubIntegrationError("GitHub path must be repository-scoped")
    request = Request(
        "https://api.github.com" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(2_000_000)
    except (HTTPError, URLError, OSError) as exc:
        raise GitHubIntegrationError(f"GitHub request failed: {exc}") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise GitHubIntegrationError("GitHub returned invalid JSON") from exc
    return result if isinstance(result, dict) else {"items": result}


def inspect_issue(owner: str, repo: str, number: int) -> dict:
    return _request("GET", f"/repos/{owner}/{repo}/issues/{int(number)}")


def inspect_pull_request(owner: str, repo: str, number: int) -> dict:
    return _request("GET", f"/repos/{owner}/{repo}/pulls/{int(number)}")


def list_pull_request_files(owner: str, repo: str, number: int) -> dict:
    return _request("GET", f"/repos/{owner}/{repo}/pulls/{int(number)}/files")


def comment_issue(
    owner: str,
    repo: str,
    number: int,
    body: str,
    *,
    approved: bool = False,
) -> dict:
    if not approved:
        raise GitHubIntegrationError("GitHub writes require explicit approval")
    if not body.strip() or len(body) > 20_000:
        raise GitHubIntegrationError("comment body is empty or too large")
    return _request(
        "POST",
        f"/repos/{owner}/{repo}/issues/{int(number)}/comments",
        {"body": body},
    )
