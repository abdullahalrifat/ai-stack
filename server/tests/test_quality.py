import json

from app.agent.quality import analyze_state, configured_candidates, evidence_audit, expert_routes


class State:
    user_message = "Refactor authentication across multiple modules"
    allow_write = True
    requires_external_evidence = False
    successful_mutation = True
    successful_verification = True


def test_heterogeneous_routes_use_distinct_models(monkeypatch):
    monkeypatch.setenv("QUALITY_MODEL_ROUTES", json.dumps([
        {"model": "coder", "quality": .9, "tool_success": .9, "structured_success": .8, "latency": .3, "cost": .2},
        {"model": "reviewer", "quality": .8, "tool_success": .8, "structured_success": .9, "latency": .2, "cost": .2},
    ]))
    routes = expert_routes(["implementation", "verification"], "fallback")
    assert routes["implementation"] != routes["verification"]


def test_adaptive_analysis_and_evidence_gate():
    assert analyze_state(State()).needs_multi_agent
    assert evidence_audit(State()).passed


def test_malformed_routes_fall_back(monkeypatch):
    monkeypatch.setenv("QUALITY_MODEL_ROUTES", "not-json")
    assert configured_candidates("default")[0].model == "default"
