"""Evaluate the live router model's translation and planning contract.

Run inside an environment with the agent's LiteLLM settings:
    PYTHONPATH=. python evals/run_router_evals.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agent.router import route_request


def main() -> int:
    cases = json.loads((Path(__file__).parent / "router_cases.json").read_text())
    failures = []
    for case in cases:
        route = route_request(case["prompt"], case.get("attachments"))
        searchable = " ".join(
            [
                route.translated_task,
                *route.entities,
                *route.constraints,
                *route.deliverables,
                *route.missing_inputs,
                *route.assumptions,
                *(task.objective for task in route.tasks),
                *(value for task in route.tasks for value in task.required_evidence),
                *(value for task in route.tasks for value in task.completion_criteria),
            ]
        ).casefold()
        missing = [
            value
            for value in case["must_preserve"]
            if value.casefold() not in searchable
        ]
        errors = []
        if route.workflow != case["workflow"]:
            errors.append(f"workflow={route.workflow}")
        if route.requires_external_evidence != case["requires_external_evidence"]:
            errors.append(f"external_evidence={route.requires_external_evidence}")
        if len(route.tasks) < case["min_tasks"]:
            errors.append(f"tasks={len(route.tasks)}")
        if route.source != "model":
            errors.append(f"source={route.source}")
        status = "PASS" if not missing and not errors else "FAIL"
        print(
            f"{status} {case['id']} ({route.workflow}, {route.complexity}, {len(route.tasks)} tasks)"
        )
        if status == "FAIL":
            failures.append({"id": case["id"], "missing": missing, "errors": errors})
    if failures:
        print(json.dumps(failures, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
