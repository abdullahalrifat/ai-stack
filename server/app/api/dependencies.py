"""Shared FastAPI dependencies for authentication and durable-run access."""

import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import AGENT_API_KEY, ALLOW_INSECURE_NO_AUTH, POSTGRES_URL

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


def verify_api_key(key: str | None = Security(api_key_header)):
    if not AGENT_API_KEY and ALLOW_INSECURE_NO_AUTH:
        return True
    if not AGENT_API_KEY:
        raise HTTPException(503, detail="Agent authentication is not configured")
    if not key:
        raise HTTPException(401, detail="Missing authorization header")
    scheme, separator, token = key.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not token or " " in token:
        raise HTTPException(401, detail="Invalid authorization header")
    if not hmac.compare_digest(token, AGENT_API_KEY):
        raise HTTPException(401, detail="Invalid API key")
    return True


def require_run_store():
    if not POSTGRES_URL:
        raise HTTPException(
            503,
            "Durable runs require POSTGRES_URL to be configured for this service.",
        )
