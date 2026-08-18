from types import SimpleNamespace
from unittest.mock import patch

from app.agent.graph import transition_graph
from app.agent.review import review_change, validate_response_claims


def _state(**overrides):
    values = {
        "graph_phase": "pending",
        "graph_history": [],
        "active_requirement": "Multi-expert dispatch with structured findings",
        "active_roadmap_item": "Multi-expert dispatch with structured findings",
        "user_message": "Implement multi-expert dispatch",
        "evidence_ledger": {
            "relevant_files": ["agents/app/agent/executor.py"],
            "test_targets": ["agents/tests/test_executor.py"],
        },
        "successful_verification": True,
        "successful_mutation_paths": {"agents/app/agent/dispatch.py"},
        "partial": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_execution_graph_accepts_valid_transitions_and_emits_events():
    state = _state()
    events = []

    assert transition_graph(
        state,
        "analyzing",
        reason="start",
        on_event=lambda kind, payload: events.append((kind, payload)),
    )
    assert transition_graph(state, "ready", reason="evidence ready")
    assert transition_graph(state, "implementing", reason="edit")
    assert transition_graph(state, "verifying", reason="tests")
    assert transition_graph(state, "reviewing", reason="diff")
    assert transition_graph(state, "complete", reason="accepted")
    assert not transition_graph(state, "repairing", reason="too late")
    assert state.graph_phase == "complete"
    assert events[0][0] == "graph_transition"


def test_deterministic_change_review_accepts_grounded_verified_diff():
    state = _state()
    diff = """diff --git a/agents/app/agent/dispatch.py b/agents/app/agent/dispatch.py
--- /dev/null
+++ b/agents/app/agent/dispatch.py
@@ -0,0 +1,2 @@
+class ExpertDispatcher:
+    findings = dispatch_experts()
"""

    result = review_change(state, diff, "Implemented expert dispatch; tests pass.")

    assert result["decision"] == "accept"
    assert result["implements_requirement"] is True


def test_change_review_rejects_unrelated_diff_and_unsupported_test_claim():
    state = _state(successful_verification=False)
    diff = """diff --git a/unrelated/worker.py b/unrelated/worker.py
--- a/unrelated/worker.py
+++ b/unrelated/worker.py
@@ -1 +1 @@
-old = 1
+new = 2
"""

    result = review_change(state, diff, "Implemented everything; tests pass.")

    assert result["decision"] == "reject"
    assert result["unrelated_changes"] == ["unrelated/worker.py"]
    assert any("verification passed" in reason for reason in result["reasons"])


def test_response_claim_validation_rejects_partial_completion_claim():
    state = _state(partial=True)
    failures = validate_response_claims(
        state,
        "Implemented and completed the feature.",
        ["agents/app/agent/dispatch.py"],
    )
    assert "response claims completion for a partial run" in failures


@patch("app.agent.review.chat", return_value='{"decision":"reject","risk":"high"}')
def test_high_risk_change_uses_independent_reviewer(mock_chat):
    state = _state(
        active_roadmap_item="",
        active_requirement="Improve security permissions",
        evidence_ledger={
            "relevant_files": ["agents/app/core/permissions.py"],
            "test_targets": ["agents/tests/test_permissions.py"],
        },
        successful_mutation_paths={"agents/app/core/permissions.py"},
    )
    diff = """diff --git a/agents/app/core/permissions.py b/agents/app/core/permissions.py
--- a/agents/app/core/permissions.py
+++ b/agents/app/core/permissions.py
@@ -1 +1 @@
-ALLOW = True
+SECURE_PERMISSION = True
"""

    result = review_change(state, diff, "Updated security permissions; tests pass.")

    assert mock_chat.called
    assert result["decision"] == "reject"
    assert "independent reviewer rejected" in result["reasons"][-1]
