"""Reliable outbound Telegram and WhatsApp delivery for terminal Runs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.request import Request, urlopen

from app.runs.store import get_run_store

logger = logging.getLogger(__name__)
DELIVERY_POLL_SECONDS = 3


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> None:
    request = Request(
        url,
        json.dumps(payload).encode(),
        {"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"channel delivery returned HTTP {response.status}")


def _deliver(item: dict) -> None:
    provider = item["provider"]
    identity = item["identity"]
    answer = str(item.get("answer") or item.get("error") or "Run finished without output")
    if provider == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        _post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": identity, "text": answer[:4096]},
        )
    elif provider == "whatsapp":
        token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        version = os.getenv("WHATSAPP_GRAPH_VERSION", "v23.0")
        if not token or not phone_id:
            raise RuntimeError(
                "WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID are required"
            )
        _post_json(
            f"https://graph.facebook.com/{version}/{phone_id}/messages",
            {
                "messaging_product": "whatsapp",
                "to": identity,
                "type": "text",
                "text": {"body": answer[:4096]},
            },
            {"Authorization": f"Bearer {token}"},
        )
    else:
        raise RuntimeError(f"unsupported outbound channel: {provider}")


def deliver_pending_once() -> int:
    store = get_run_store()
    delivered = 0
    for item in store.claim_channel_deliveries(limit=20):
        try:
            _deliver(item)
        except Exception as exc:
            logger.warning("Channel delivery failed for %s: %s", item["run_id"], exc)
            store.finish_channel_delivery(
                item["provider"], item["event_id"], error=str(exc)
            )
        else:
            store.finish_channel_delivery(item["provider"], item["event_id"])
            delivered += 1
    return delivered


async def monitor_channel_deliveries() -> None:
    while True:
        try:
            await asyncio.to_thread(deliver_pending_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Channel delivery monitor failed")
        await asyncio.sleep(DELIVERY_POLL_SECONDS)
