"""Expected outcomes for distributed cloud-task lifecycle fault evaluation.

This is a deterministic contract matrix, not shared-host chaos certification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChaosCase:
    id: str
    fault: str
    expected: str


CASES = (
    ChaosCase("server-restart", "server_restart", "reclaimable"),
    ChaosCase("worker-restart", "worker_restart", "reclaimable"),
    ChaosCase("redis-outage", "redis_outage", "retryable"),
    ChaosCase("postgres-outage", "postgres_outage", "retryable"),
    ChaosCase("network-partition", "network_partition", "lease-fenced"),
    ChaosCase("lease-expiry", "lease_expiry", "stale-worker-rejected"),
    ChaosCase("duplicate-completion", "duplicate_completion", "idempotent"),
    ChaosCase("cancellation-race", "cancellation_race", "terminal-cancel"),
    ChaosCase("disk-state-failure", "disk_state_failure", "failure-recorded"),
    ChaosCase("telemetry-outage", "telemetry_outage", "execution-preserved"),
)


def validate_case(case: ChaosCase, observed: str) -> bool:
    return observed == case.expected
