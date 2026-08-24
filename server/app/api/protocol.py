"""Versioned contract shared by terminal clients and the Runs API."""

from __future__ import annotations

from fastapi import Header, HTTPException

PROTOCOL_VERSION = 1
MIN_CLI_PROTOCOL_VERSION = 1
MAX_CLI_PROTOCOL_VERSION = 1
EVENT_SCHEMA_VERSION = 1
PROTOCOL_HEADER = "X-Jarvis-Protocol-Version"
FEATURES = [
    "background_schedules",
    "cancellable_runner_jobs",
    "client_leases",
    "cloud_worker_leases",
    "durable_events",
    "capability_model_routing",
    "citation_aware_web_search",
    "content_addressed_traces",
    "empirical_route_calibration",
    "evaluation_replay",
    "opentelemetry",
    "review_sandboxes",
    "runner_job_leases",
    "stream_heartbeats",
    "versioned_events",
]


def verify_protocol_version(
    x_jarvis_protocol_version: str | None = Header(None),
) -> None:
    """Reject an explicitly incompatible client with upgrade instructions.

    A missing header remains accepted for the OpenAI-compatible API and older
    integrations. First-party clients always send the header.
    """

    if x_jarvis_protocol_version is None:
        return
    try:
        requested = int(x_jarvis_protocol_version)
    except ValueError as exc:
        raise HTTPException(
            400,
            f"{PROTOCOL_HEADER} must be an integer protocol version.",
        ) from exc
    if not MIN_CLI_PROTOCOL_VERSION <= requested <= MAX_CLI_PROTOCOL_VERSION:
        raise HTTPException(
            426,
            "Incompatible Jarvis CLI protocol "
            f"{requested}; server supports "
            f"{MIN_CLI_PROTOCOL_VERSION}..{MAX_CLI_PROTOCOL_VERSION}. "
            "Upgrade the CLI or server so their protocol ranges overlap.",
        )


def event_envelope(event: dict) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": EVENT_SCHEMA_VERSION,
        **event,
    }
