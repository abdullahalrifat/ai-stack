"""v0.9 signed Slack and GitHub issue-event adapters using the shared run queue."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.dependencies import require_run_store
from app.channels.router import _queue

router = APIRouter(prefix="/channels/v09", tags=["channels-v09"])


def _hmac_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@router.post("/slack/events", dependencies=[Depends(require_run_store)])
async def slack_events(
    request: Request,
    x_slack_request_timestamp: str | None = Header(default=None),
    x_slack_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    secret = os.getenv("SLACK_SIGNING_SECRET")
    if not secret or not x_slack_request_timestamp or not x_slack_signature:
        raise HTTPException(401, "missing Slack signature")
    try:
        timestamp = int(x_slack_request_timestamp)
    except ValueError as exc:
        raise HTTPException(401, "invalid Slack timestamp") from exc
    if abs(int(time.time()) - timestamp) > 300:
        raise HTTPException(401, "stale Slack request")
    raw = await request.body()
    base = b"v0:" + x_slack_request_timestamp.encode() + b":" + raw
    expected = "v0=" + _hmac_hex(secret, base)
    if not hmac.compare_digest(expected, x_slack_signature):
        raise HTTPException(401, "invalid Slack signature")
    payload = await request.json()
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}
    event = payload.get("event") or {}
    if event.get("bot_id") or event.get("subtype"):
        return {"status": "ignored"}
    text = str(event.get("text") or "").strip()
    user = str(event.get("user") or "")
    channel = str(event.get("channel") or "")
    event_id = str(payload.get("event_id") or "")
    if not text or not user or not channel or not event_id:
        raise HTTPException(400, "unsupported Slack event")
    return await _queue(
        provider="slack",
        event_id=event_id,
        identity=user,
        task=text,
        payload=payload,
        conversation_id=f"slack:{channel}",
    )


@router.post("/github/issues", dependencies=[Depends(require_run_store)])
async def github_issue_event(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
) -> dict[str, Any]:
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    raw = await request.body()
    if not secret or not x_hub_signature_256 or not x_hub_signature_256.startswith("sha256="):
        raise HTTPException(401, "missing GitHub webhook signature")
    expected = _hmac_hex(secret, raw)
    if not hmac.compare_digest(expected, x_hub_signature_256[7:]):
        raise HTTPException(401, "invalid GitHub webhook signature")
    if x_github_event not in {"issues", "issue_comment"}:
        return {"status": "ignored"}
    payload = await request.json()
    action = str(payload.get("action") or "")
    if action not in {"opened", "reopened", "created"}:
        return {"status": "ignored"}
    issue = payload.get("issue") or {}
    repo = payload.get("repository") or {}
    sender = payload.get("sender") or {}
    labels = {str(item.get("name") or "") for item in issue.get("labels") or []}
    required_label = os.getenv("JARVIS_GITHUB_ISSUE_LABEL", "jarvis")
    if required_label and required_label not in labels:
        return {"status": "ignored", "reason": "label"}
    body = (
        str((payload.get("comment") or {}).get("body") or "")
        if x_github_event == "issue_comment"
        else str(issue.get("body") or "")
    )
    task = (
        f"GitHub issue workflow for {repo.get('full_name')} #{issue.get('number')}: "
        f"{issue.get('title') or ''}\n\n{body}"
    )
    delivery = x_github_delivery or str(payload.get("hook_id") or "")
    if not delivery or not issue.get("number"):
        raise HTTPException(400, "unsupported GitHub issue payload")
    return await _queue(
        provider="github",
        event_id=delivery,
        identity=str(sender.get("login") or "github"),
        task=task,
        payload=payload,
        conversation_id=f"github:{repo.get('full_name')}:{issue.get('number')}",
    )
