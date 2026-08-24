import json

from app.agent.quality import analyze_state, configured_candidates, evidence_audit, expert_routes


class State:
    user_message = "Refactor authentication across multiple modules"
    allow_write = True
    requires_external_evidence = False
    mutation_events = [
        {
            "success": True,
            "path": "app/auth.py",
            "before_sha256": "before",
            "after_sha256": "after",
        }
    ]
    verification_events = [
        {
            "kind": "test",
            "command": "pytest -q",
            "exit_code": 0,
            "stdout_sha256": "stdout",
        }
    ]


def test_heterogeneous_routes_preserve_provider_and_use_distinct_models(monkeypatch):
    monkeypatch.setenv(
        "QUALITY_MODEL_ROUTES",
        json.dumps(
            [
                {
                    "profile": "coder-local",
                    "model": "coder",
                    "provider": "ollama",
                    "quality": 0.9,
                    "tool_success": 0.9,
                    "structured_success": 0.8,
                    "latency": 0.3,
                    "cost": 0.2,
                    "roles": ["implementation"],
                },
                {
                    "profile": "reviewer-remote",
                    "model": "reviewer",
                    "provider": "anthropic",
                    "quality": 0.8,
                    "tool_success": 0.8,
                    "structured_success": 0.9,
                    "latency": 0.2,
                    "cost": 0.2,
                    "roles": ["verification"],
                },
            ]
        ),
    )
    routes = expert_routes(["implementation", "verification"], "fallback")
    assert routes["implementation"].model != routes["verification"].model
    assert routes["implementation"].provider == "ollama"
    assert routes["verification"].provider == "anthropic"


def test_adaptive_analysis_and_execution_backed_evidence_gate():
    assert analyze_state(State()).needs_multi_agent
    assert evidence_audit(State()).passed


def test_runtime_state_rejects_boolean_success_without_execution_proof():
    class RuntimeState:
        user_message = "Refactor authentication across multiple modules"
        allow_write = True
        requires_external_evidence = False
        successful_mutation = True
        successful_verification = True
        mutation_events = []
        verification_events = []

    audit = evidence_audit(RuntimeState())
    assert not audit.passed
    assert set(audit.missing) == {"workspace mutation", "verification"}


def test_legacy_test_double_remains_compatible():
    class LegacyState:
        user_message = "Refactor authentication across multiple modules"
        allow_write = True
        requires_external_evidence = False
        successful_mutation = True
        successful_verification = True

    assert evidence_audit(LegacyState()).passed


def test_malformed_routes_fall_back(monkeypatch):
    monkeypatch.setenv("QUALITY_MODEL_ROUTES", "not-json")
    assert configured_candidates("default")[0].model == "default"
