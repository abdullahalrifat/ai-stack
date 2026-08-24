from pathlib import Path
from types import SimpleNamespace

from app.api.protocol import FEATURES
from app.platform.efficiency_v07 import (
    _observed_failures,
    _risk_score,
    evidence_confidence,
    failure_kind,
    retry_action,
    task_category,
)
from app.platform.failure_store_v07 import failure_fingerprint


def test_task_category_and_failure_taxonomy():
    assert task_category("fix login regression") == "bugfix"
    assert task_category("review auth permission") == "security"
    assert failure_kind("HTTP 429 rate limit") == "rate_limit"
    assert failure_kind("pytest assertionerror") == "test"
    assert retry_action("rate_limit", 1)["retry"]
    assert retry_action("test", 3)["escalate"]


def test_observed_failures_increase_risk_and_escalation_signal():
    state = SimpleNamespace(
        user_message="production auth migration",
        execution_ledger={
            "verification_events": [
                {"exit_code": 1},
                {"exit_code": 1},
            ]
        },
    )
    assert _observed_failures(state) == 2
    assert _risk_score(state) >= 0.65


def test_evidence_confidence_is_execution_derived():
    strong = SimpleNamespace(
        allow_write=True,
        mutation_events=[{"success": True}],
        verification_events=[{"kind": "test", "exit_code": 0}],
        execution_ledger={},
    )
    weak = SimpleNamespace(
        allow_write=True,
        mutation_events=[],
        verification_events=[{"kind": "test", "exit_code": 1}],
        execution_ledger={"verification_events": [{"exit_code": 1}]},
    )
    assert evidence_confidence(strong) > evidence_confidence(weak)


def test_failure_fingerprint_is_stable_and_route_specific():
    first = failure_fingerprint("test", "coder", " Assertion failed  ")
    second = failure_fingerprint("test", "coder", "assertion   failed")
    other = failure_fingerprint("test", "reasoning", "assertion failed")
    assert first == second
    assert first != other


def test_v07_protocol_features_are_advertised():
    expected = {
        "adaptive_context_compilation",
        "failure_driven_escalation",
        "evidence_confidence",
        "persistent_failure_memory",
        "task_category_route_calibration",
    }
    assert expected.issubset(set(FEATURES))


def test_v07_failure_migration_is_idempotent_and_indexed():
    migration = (
        Path(__file__).parents[1]
        / "app"
        / "runs"
        / "migrations"
        / "008_efficiency_reliability.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS agent_failure_signatures" in migration
    assert "CREATE TABLE IF NOT EXISTS agent_failure_run_observations" in migration
    assert "fingerprint TEXT PRIMARY KEY" in migration
    assert "run_id TEXT PRIMARY KEY" in migration
    assert "idx_agent_failure_signatures_category" in migration
