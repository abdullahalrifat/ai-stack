from app.platform.lineage_runtime import bind_findings


def test_bind_findings_creates_parent_child_proof_chain():
    findings = [{"expert": "architecture", "findings": [{"claim": "x", "evidence": ["a"]}]}]
    events = []
    bound = bind_findings(findings, "run-123", on_event=lambda kind, payload: events.append((kind, payload)))
    lineage = bound[0]["lineage"]
    assert lineage["parent_task_id"] == "run-123"
    assert lineage["root_task_id"] == "run-123"
    assert lineage["depth"] == 1
    assert lineage["proof_digest"]
    assert events[0][0] == "expert_lineage_bound"
