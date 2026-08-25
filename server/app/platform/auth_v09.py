"""Tenant-bound service token authentication for v0.9 platform endpoints."""

from __future__ import annotations

import hmac
import json
import os

from fastapi import Header, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import AGENT_API_KEY, ALLOW_INSECURE_NO_AUTH

_auth_header = APIKeyHeader(name="Authorization", auto_error=False)


def _tokens() -> dict[str, str]:
    raw = os.getenv("JARVIS_TENANT_SERVICE_TOKENS", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(503, "invalid JARVIS_TENANT_SERVICE_TOKENS JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(503, "tenant service tokens must be a JSON object")
    return {
        str(tenant): str(token)
        for tenant, token in payload.items()
        if str(tenant).strip() and str(token)
    }


def authenticate_v09(
    authorization: str | None = Security(_auth_header),
    x_jarvis_tenant: str | None = Header(default=None),
) -> str:
    """Return ``*`` for the administrator key or the authenticated tenant ID."""
    if not authorization:
        if ALLOW_INSECURE_NO_AUTH and not AGENT_API_KEY and not _tokens():
            return "*"
        raise HTTPException(401, "missing authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if AGENT_API_KEY and hmac.compare_digest(token, AGENT_API_KEY):
        return "*"
    tenant = (x_jarvis_tenant or "").strip()
    expected = _tokens().get(tenant)
    if not tenant or expected is None or not hmac.compare_digest(token, expected):
        raise HTTPException(401, "invalid tenant service token")
    return tenant


def enforce_tenant(authenticated: str, requested: str) -> None:
    if authenticated != "*" and not hmac.compare_digest(authenticated, requested):
        raise HTTPException(403, "service token is not authorized for requested tenant")
