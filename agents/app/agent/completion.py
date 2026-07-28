"""Deterministic execution-progress tracking and final-answer acceptance."""

import re


_CHANGE_REQUEST = re.compile(
    r"\b(?:add|build|change|create|edit|fix|implement|improve|modify|"
    r"refactor|remove|rename|replace|update|write)\b",
    re.IGNORECASE,
)
_VERIFICATION_REQUEST = re.compile(
    r"\b(?:build|check|compile|coverage|lint|test|verify)\b",
    re.IGNORECASE,
)
_VERIFICATION_COMMANDS = {
    "black",
    "flake8",
    "make",
    "mypy",
    "npm",
    "pytest",
    "ruff",
}


def tool_result_failed(result) -> bool:
    """Recognize registry errors and non-zero command exit codes."""

    if not result:
        return True
    if not isinstance(result, dict):
        return False
    if result.get("error") or result.get("tool_error"):
        return True
    exit_code = result.get("exit_code")
    return (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code != 0
    )


def _tool_category(tool_name: str, args: dict) -> str:
    if tool_name in {"write_file", "edit_file"}:
        return "mutation"
    if tool_name == "run_tests":
        return "verification"
    if tool_name == "run_command":
        command = str(args.get("command", "")).strip()
        executable = command.split(maxsplit=1)[0] if command else ""
        return (
            "verification"
            if executable in _VERIFICATION_COMMANDS
            else "command"
        )
    if tool_name in {"web_search", "web_fetch"}:
        return "external_evidence"
    return "inspection"


def record_tool_progress(state, tool_name: str, args: dict, result) -> None:
    """Update deterministic completion state from an observed tool result."""

    category = _tool_category(tool_name, args)
    pending = set(getattr(state, "pending_failure_categories", set()))
    if tool_result_failed(result):
        pending.add(category)
        state.pending_failure_categories = pending
        return

    pending.discard(category)
    state.pending_failure_categories = pending
    if category == "mutation":
        state.successful_mutation = True
    elif category == "verification":
        state.successful_verification = True


def _criterion_satisfied(state, criterion: str, answer: str) -> bool:
    lowered = criterion.casefold()
    if _VERIFICATION_REQUEST.search(lowered):
        return state.successful_verification
    if _CHANGE_REQUEST.search(lowered):
        return state.successful_mutation
    if re.search(r"\b(?:evidence|inspect|source|file|document)\b", lowered):
        return bool(state.observations)
    keywords = [
        token
        for token in re.findall(r"[a-z0-9]{4,}", lowered)
        if token
        not in {
            "address",
            "complete",
            "completed",
            "every",
            "explicit",
            "original",
            "part",
            "request",
            "requested",
        }
    ]
    answer_lower = answer.casefold()
    return not keywords or any(keyword in answer_lower for keyword in keywords)


def answer_audit(state, answer: str) -> list[str]:
    """Check observable route requirements before accepting a final answer."""

    lowered = answer.casefold()
    failures: list[str] = []
    missing_entities = [
        entity
        for entity in getattr(state, "routing_entities", [])[:30]
        if entity.casefold() not in lowered
    ]
    if missing_entities:
        failures.append("missing grounded entities: " + ", ".join(missing_entities))

    change_requested = bool(_CHANGE_REQUEST.search(state.user_message))
    verification_requested = bool(_VERIFICATION_REQUEST.search(state.user_message))
    if getattr(state, "allow_write", False) and change_requested:
        if not getattr(state, "successful_mutation", False):
            failures.append("requested workspace change has not been made")
        if verification_requested and not getattr(
            state, "successful_verification", False
        ):
            failures.append("requested verification has not completed successfully")

    relevant_failure_categories = {"inspection"}
    if getattr(state, "allow_write", False) and change_requested:
        relevant_failure_categories.add("mutation")
    if verification_requested:
        relevant_failure_categories.add("verification")
    if getattr(state, "requires_external_evidence", False):
        relevant_failure_categories.add("external_evidence")
    pending_failures = sorted(
        set(getattr(state, "pending_failure_categories", set()) or set())
        & relevant_failure_categories
    )
    if pending_failures:
        failures.append(
            "unresolved failed tool categories: " + ", ".join(pending_failures)
        )

    for deliverable in getattr(state, "route_deliverables", [])[:12]:
        keywords = [
            token
            for token in re.findall(r"[a-z0-9]{4,}", deliverable.casefold())
            if token not in {"provide", "include", "analysis", "summary"}
        ]
        if keywords and not any(keyword in lowered for keyword in keywords):
            failures.append(f"deliverable may be missing: {deliverable}")

    task_progress = getattr(state, "task_progress", {})
    for task in getattr(state, "route_tasks", [])[:8]:
        criteria = task.get("completion_criteria") or []
        complete = all(
            _criterion_satisfied(state, str(criterion), answer)
            for criterion in criteria
        )
        task_id = str(task.get("id", "task"))
        task_progress[task_id] = "completed" if complete else "pending"
        if not complete:
            failures.append(f"task completion criteria not met: {task_id}")

    evidence = getattr(state, "document_evidence", {}) or {}
    if evidence and evidence.get("provenance_required"):
        sources = {
            str(record.get("source")).casefold()
            for record in evidence.get("records", [])
            if record.get("source")
        }
        if sources and not any(source in lowered for source in sources):
            failures.append("document provenance is not cited")
        excluded_entities = [
            entity
            for entity in evidence.get("excluded_entities", [])
            if len(entity) >= 5 and entity.casefold() in lowered
        ]
        if excluded_entities:
            failures.append(
                "uses entities found only in excluded document sections: "
                + ", ".join(excluded_entities[:12])
            )
    return failures
