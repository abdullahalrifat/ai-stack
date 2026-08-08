from unittest.mock import patch

from app.agent.dispatch import (
    _select_experts,
    _validate_finding,
    dispatch_experts,
    findings_context,
)
from app.agent.state import AgentState


def _state(**overrides):
    defaults = {
        "conversation_id": "test",
        "user_message": "Implement parallel subagents in the runner",
        "allow_write": True,
        "execution_brief": "Translated objective:\nImplement Tier 3",
        "route_tasks": [
            {"id": "t1", "objective": "Dispatch experts", "workflow": "code"}
        ],
    }
    defaults.update(overrides)
    return AgentState(**defaults)


def test_select_experts_for_read_only_analysis_is_just_architecture():
    state = _state(
        user_message="Review the schema of app/core/config.py",
        allow_write=False,
        requires_external_evidence=False,
    )
    assert _select_experts(state) == ["architecture"]


def test_select_experts_for_workspace_change_adds_implementation_and_verification():
    state = _state(
        user_message="Implement the missing retry logic in client.py",
        allow_write=True,
        requires_external_evidence=False,
    )
    assert _select_experts(state) == [
        "architecture",
        "implementation",
        "verification",
    ]


def test_select_experts_adds_risk_for_risk_signals_and_respects_cap():
    state = _state(user_message="Refactor the auth module to remove migration risk")
    roles = _select_experts(state)
    assert roles == ["architecture", "implementation", "verification", "risk"]


def test_select_experts_caps_roles_for_a_risk_write_request():
    state = _state(user_message="Refactor the auth module to remove migration risk")
    assert len(_select_experts(state)) <= 4


def test_select_experts_external_evidence_includes_implementation():
    state = _state(
        user_message="Compare this repo against the latest stable release",
        allow_write=False,
        requires_external_evidence=True,
    )
    assert _select_experts(state) == ["architecture", "implementation"]


def test_validate_finding_normalizes_and_bounds_arbitrary_expert_json():
    data = {
        "expert": "architecture",
        "findings": [
            {
                "claim": "short",
                "evidence": ["a.py", "b.py"],
                "confidence": "HIGH",
            },
            {"claim": "x" * 500, "evidence": "not-a-list", "confidence": "maybe"},
            {"claim": "y"},
            {"claim": "z", "evidence": list(range(20))},
        ],
        "open_questions": ["q1", "q2", "q3", "q4"],
        "recommended_focus": ["f1"],
    }
    finding = _validate_finding("architecture", data)
    assert finding["expert"] == "architecture"
    assert len(finding["findings"]) == 4
    first = finding["findings"][0]
    assert first["confidence"] == "high"  # "HIGH" case-folded to a valid level
    assert len(finding["findings"][1]["claim"]) <= 240
    assert finding["findings"][1]["evidence"] == []  # non-list dropped
    assert finding["findings"][3]["evidence"] == []  # numeric evidence dropped
    assert finding["open_questions"] == ["q1", "q2", "q3"]


def test_validate_finding_handles_junk_input():
    for junk in (None, "text", [], {"findings": "nope"}):
        finding = _validate_finding("risk", junk)
        assert finding["expert"] == "risk"
        assert finding["findings"] == []
        assert finding["open_questions"] == []
        assert finding["recommended_focus"] == []


@patch("app.agent.dispatch.chat")
def test_dispatch_experts_runs_all_roles_in_parallel(mock_chat):
    state = _state()
    mock_chat.side_effect = [
        '{"expert":"architecture","findings":[{"claim":"found owner","evidence":["src/app.py"],"confidence":"high"}],"open_questions":[],"recommended_focus":["src/app.py"]}',
        '{"expert":"implementation","findings":[{"claim":"add class","evidence":["src/app.py"],"confidence":"medium"}],"open_questions":[],"recommended_focus":[]}',
        '{"expert":"verification","findings":[{"claim":"add tests","evidence":[],"confidence":"low"}],"open_questions":[],"recommended_focus":[]}',
    ]
    events = []

    findings = dispatch_experts(
        state, on_event=lambda kind, payload: events.append((kind, payload))
    )

    assert mock_chat.call_count == 3
    roles = {item["expert"] for item in findings}
    assert roles == {"architecture", "implementation", "verification"}
    high = next(item for item in findings if item["expert"] == "architecture")
    assert high["findings"][0]["confidence"] == "high"
    assert events[0][0] == "expert_dispatch"
    assert events[0][1]["roles"] == ["architecture", "implementation", "verification"]
    assert events[0][1]["total"] == 3
    assert any(kind == "expert_findings" for kind, _ in events)


@patch("app.agent.dispatch.chat")
def test_dispatch_experts_isolates_failed_expert(mock_chat):
    state = _state()
    mock_chat.side_effect = [
        RuntimeError("gateway down"),
        '{"findings":[{"claim":"ok"}]}',
        '{"findings":[{"claim":"ok2"}]}',
    ]

    findings = dispatch_experts(state)

    roles = {item["expert"] for item in findings}
    assert roles == {"architecture", "implementation", "verification"}
    failed = next(item for item in findings if item["expert"] == "architecture")
    assert failed["findings"][0]["confidence"] == "low"
    assert "unavailable" in failed["findings"][0]["claim"]


@patch("app.agent.dispatch.chat")
def test_dispatch_experts_read_only_analysis_runs_only_architecture(mock_chat):
    state = _state(
        user_message="Explain config.py",
        allow_write=False,
        requires_external_evidence=False,
    )
    mock_chat.side_effect = [
        '{"expert":"architecture","findings":[],"open_questions":[],"recommended_focus":[]}'
    ]

    findings = dispatch_experts(state)

    assert mock_chat.call_count == 1
    assert [item["expert"] for item in findings] == ["architecture"]


def test_findings_context_renders_only_substantive_findings():
    findings = [
        {
            "expert": "architecture",
            "findings": [
                {"claim": "owner found", "evidence": ["a.py"], "confidence": "high"}
            ],
            "open_questions": [],
            "recommended_focus": [],
        },
        {
            "expert": "risk",
            "findings": [],
            "open_questions": [],
            "recommended_focus": [],
        },
    ]
    text = findings_context(findings)
    assert "architecture: [high] owner found (evidence: a.py)" in text
    assert "risk" not in text
    assert findings_context([]) == ""
