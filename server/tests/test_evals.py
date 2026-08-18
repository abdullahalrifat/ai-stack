import json
from pathlib import Path


def test_eval_cases_have_stable_contracts():
    cases = json.loads((Path(__file__).parents[1] / "evals" / "cases.json").read_text())

    assert len(cases) >= 3
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["profile"] in {"code", "finance", "research", "quick", "deep"}
        assert case["prompt"].strip()
        assert isinstance(case["must_contain"], list)
        assert isinstance(case["forbid"], list)


def test_router_eval_cases_have_stable_contracts():
    cases = json.loads(
        (Path(__file__).parents[1] / "evals" / "router_cases.json").read_text()
    )

    assert cases
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["workflow"] in {
            "quick",
            "code",
            "research",
            "finance",
            "deep",
            "vision",
        }
        assert case["prompt"].strip()
        assert isinstance(case["attachments"], list)
        assert isinstance(case["must_preserve"], list) and case["must_preserve"]
        assert isinstance(case["requires_external_evidence"], bool)
        assert case["min_tasks"] >= 1
