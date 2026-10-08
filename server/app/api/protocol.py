"""Versioned contract shared by terminal clients and the Runs API."""

from __future__ import annotations

from fastapi import Header, HTTPException

PROTOCOL_VERSION = 1
MIN_CLI_PROTOCOL_VERSION = 1
MAX_CLI_PROTOCOL_VERSION = 1
EVENT_SCHEMA_VERSION = 1
PROTOCOL_HEADER = "X-Jarvis-Protocol-Version"
FEATURES = [
    "adaptive_context_compiler", "adaptive_context_compilation", "background_schedules",
    "cancellable_runner_jobs", "client_leases", "cloud_worker_leases", "cloud_lease_fencing",
    "cloud_git_workspaces", "cloud_idempotency_keys", "cloud_execution_states", "cloud_proof_ledger",
    "durable_events", "capability_model_routing", "citation_aware_web_search", "content_addressed_traces",
    "dynamic_model_escalation", "failure_driven_escalation", "empirical_route_calibration", "evidence_confidence",
    "evaluation_replay", "failure_signature_memory", "persistent_failure_memory", "independent_verification",
    "opentelemetry", "portable_cloud_model_profiles", "review_sandboxes", "runner_job_leases",
    "standard_cron_semantics", "stream_heartbeats", "task_category_route_calibration", "versioned_events",
    "inference_provider_status", "inference_diagnostics", "model_usage_cost_telemetry", "github_issue_branch_pr_loop", "github_ci_review_status",
    "ide_jsonrpc_protocol", "subagent_lineage_proofs", "backup_restore_dr_certification", "worker_soak_harness",
    "optional_tenant_identity",
]


def verify_protocol_version(x_jarvis_protocol_version: str | None = Header(None)) -> None:
    if x_jarvis_protocol_version is None:
        return
    try:
        requested = int(x_jarvis_protocol_version)
    except ValueError as exc:
        raise HTTPException(400, f"{PROTOCOL_HEADER} must be an integer protocol version.") from exc
    if not MIN_CLI_PROTOCOL_VERSION <= requested <= MAX_CLI_PROTOCOL_VERSION:
        raise HTTPException(426, "Incompatible Jarvis CLI protocol " f"{requested}; server supports {MIN_CLI_PROTOCOL_VERSION}..{MAX_CLI_PROTOCOL_VERSION}. Upgrade the CLI or server so their protocol ranges overlap.")


def event_envelope(event: dict) -> dict:
    return {"protocol_version": PROTOCOL_VERSION, "schema_version": EVENT_SCHEMA_VERSION, **event}
