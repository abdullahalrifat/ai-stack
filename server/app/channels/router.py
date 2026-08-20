"""Web, Telegram, and WhatsApp ingress adapters with one durable agent loop."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.agent.service import submit_run
from app.api.dependencies import require_run_store, verify_api_key
from app.core.config import AGENT_MODEL_ID
from app.runs.store import get_run_store
from app.tools.filesystem import resolve_request_workspace

router = APIRouter(prefix="/channels", tags=["channels"])


class WebRun(BaseModel):
    task: str
    workspace: str | None = None
    conversation_id: str | None = None
    client_id: str | None = None


async def _queue(
    *,
    provider: str,
    event_id: str,
    identity: str,
    task: str,
    payload: dict[str, Any],
    workspace: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    if not task.strip():
        raise HTTPException(400, "channel message is empty")
    try:
        resolved = resolve_request_workspace(workspace, task)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc)) from exc
    run_id, created = await run_in_threadpool(
        get_run_store().create_channel_run,
        provider=provider,
        event_id=event_id,
        identity=identity,
        payload=payload,
        task=task,
        model=AGENT_MODEL_ID,
        workspace=resolved,
        conversation_id=conversation_id,
    )
    if created:
        submit_run(run_id)
    return {"run_id": run_id, "status": "queued" if created else "duplicate"}


@router.post(
    "/web/runs",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def web_run(request: WebRun) -> dict[str, Any]:
    import uuid

    return await _queue(
        provider="web",
        event_id=str(uuid.uuid4()),
        identity=request.client_id or "web",
        task=request.task,
        payload=request.model_dump(),
        workspace=request.workspace,
        conversation_id=request.conversation_id,
    )


@router.post("/telegram/webhook", dependencies=[Depends(require_run_store)])
async def telegram_webhook(
    payload: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if not secret or not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", secret
    ):
        raise HTTPException(401, "invalid Telegram webhook secret")
    message = payload.get("message") or payload.get("edited_message") or {}
    chat = message.get("chat") or {}
    text = str(message.get("text") or message.get("caption") or "")
    event_id = str(payload.get("update_id") or "")
    if not event_id or not chat.get("id"):
        raise HTTPException(400, "unsupported Telegram update")
    return await _queue(
        provider="telegram",
        event_id=event_id,
        identity=str(chat["id"]),
        task=text,
        payload=payload,
        conversation_id=f"telegram:{chat['id']}",
    )


def _verify_whatsapp(raw: bytes, signature: str | None) -> None:
    secret = os.getenv("WHATSAPP_APP_SECRET")
    if not secret or not signature or not signature.startswith("sha256="):
        raise HTTPException(401, "missing WhatsApp signature")
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature[7:], expected):
        raise HTTPException(401, "invalid WhatsApp signature")


@router.get("/whatsapp/webhook", response_class=PlainTextResponse)
async def whatsapp_verify(request: Request) -> str:
    query = request.query_params
    if (
        query.get("hub.mode") != "subscribe"
        or query.get("hub.verify_token") != os.getenv("WHATSAPP_VERIFY_TOKEN")
    ):
        raise HTTPException(403, "invalid WhatsApp verification token")
    return query.get("hub.challenge", "0")


@router.post("/whatsapp/webhook", dependencies=[Depends(require_run_store)])
async def whatsapp_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, Any]:
    raw = await request.body()
    _verify_whatsapp(raw, x_hub_signature_256)
    payload = await request.json()
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        message = value["messages"][0]
        identity = str(message["from"])
        event_id = str(message["id"])
        text = str(message.get("text", {}).get("body") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(400, "unsupported WhatsApp webhook payload") from exc
    return await _queue(
        provider="whatsapp",
        event_id=event_id,
        identity=identity,
        task=text,
        payload=payload,
        conversation_id=f"whatsapp:{identity}",
    )
