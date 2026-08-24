"""Deterministic execution-progress tracking and final-answer acceptance."""

import hashlib
import json
import re

from .quality import evidence_audit

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

_IMPLEMENTATION_INTENT = re.compile(
    r"\b(?:implement|build|refactor|fix|repair|develop|create)\b",
    re.IGNORECASE,
)
_DOCUMENTATION_REQUEST = re.compile(
    r"\b(?:document|readme|changelog|docs?|write(?:ing)? (?:a|the|up) )\b",
    re.IGNORECASE,
)
_DOCUMENTATION_SUFFIXES = (".md", ".rst", ".txt")
DOC_ONLY_MUTATION_FAILURE = (
    "change only modified documentation/marker files "
    "(TODO/README/roadmap); an implementation request must change "
    "code files"
)


def is_implementation_request(message: str) -> bool:
    return bool(
        _IMPLEMENTATION_INTENT.search(message)
        and not _DOCUMENTATION_REQUEST.search(message)
    )


def is_documentation_path(path: str) -> bool:
    normalized = str(path).strip().lstrip("./").casefold()
    return normalized.endswith(_DOCUMENTATION_SUFFIXES)


def tool_result_failed(result) -> bool:
    if not result:
        return True
    if not isinstance(result, dict):
        return False
    if result.get("error") or result.get("tool_error"):
        return True
    items = result.get("items")
    if (
        isinstance(items, list)
        and items
        and all(
            isinstance(item, dict) and (item.get("error") or item.get("tool_error"))
            for item in items
        )
    ):
        return True
    exit_code = result.get("exit_code")
    return (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code != 0
    )


def requires_workspace_change(message: str) -> bool:
    return bool(_CHANGE_REQUEST.search(message))


def _tool_category(tool_name: str, args: dict) -> str:
    if tool_name in {"write_file", "edit_file", "apply_patch"}:
        return "mutation"
    if tool_name == "run_tests":
        return "verification"
    if tool_name == "run_command":
        command = str(args.get("command", "")).strip()
        executable = command.split(maxsplit=1)[0] if command else ""
        return "verification" if executable in _VERIFICATION_COMMANDS else "command"
    if tool_name in {"web_search", "web_fetch"}:
        return "external_evidence"
    return "inspection"


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _record_execution_evidence(state, category: str, tool_name: str, args: dict, result) -> None:
    """Append immutable evidence derived from observed tool execution."""
    if category == "mutation":
        path = str(args.get("file_path") or args.get("path") or "").strip().lstrip("./")
        if not path:
            path = "workspace"
        events = list(getattr(state, "mutation_events", []) or [])
        events.append(
            {
                "success": True,
                "tool": tool_name,
                "path": path,
                "args_sha256": _digest(args),
                "result_sha256": _digest(result),
            }
        )
        state.mutation_events = events
    elif category == "verification":
        exit_code = result.get("exit_code", 0) if isinstance(result, dict) else 0
        events = list(getattr(state, "verification_events", []) or [])
        events.append(
            {
                "kind": "test" if tool_name == "run_tests" else "command",
                "tool": tool_name,
                "command": str(args.get("command") or tool_name),
                "exit_code": exit_code,
                "args_sha256": _digest(args),
                "stdout_sha256": _digest(result),
            }
        )
        state.verification_events = events


def record_tool_progress(state, tool_name: str, args: dict, result) -> None:
    """Update completion state and execution-backed evidence from a tool result."""
    category = _tool_category(tool_name, args)
    pending = set(getattr(state, "pending_failure_categories", set()))
    successful = set(getattr(state, "successful_tool_categories", set()))
    if tool_result_failed(result):
        if category != "inspection" or category not in successful:
            pending.add(category)
        state.pending_failure_categories = pending
        return

    successful.add(category)
    state.successful_tool_categories = successful
    pending.discard(category)
    state.pending_failure_categories = pending
    _record_execution_evidence(state, category, tool_name, args, result)

    if category == "mutation":
        state.successful_mutation = True
        state.successful_verification = False
        state.verification_events = []
        file_path = str(args.get("file_path") or args.get("path") or "").strip().lstrip("./")
        if file_path:
            mutated = set(getattr(state, "successful_mutation_paths", set()))
            mutated.add(file_path)
            state.successful_mutation_paths = mutated
    elif category == "verification":
        state.successful_verification = True


def _criterion_satisfied(state, criterion: str, answer: str) -> bool:
    lowered = criterion.casefold()
    if re.search(r"\b(?:report|summary|synthesi[sz]\w*|recommend\w*)\b", lowered):
        return len(answer.strip()) >= 80 and bool(state.observations)
    if _VERIFICATION_REQUEST.search(lowered):
        return getattr(state, "successful_verification", False)
    if _CHANGE_REQUEST.search(lowered):
        return getattr(state, "successful_mutation", False)
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


def _has_successful_verification(state) -> bool:
    if getattr(state, "successful_verification", False):
        return True
    for observation in reversed(getattr(state, "observations", []) or []):
        if not isinstance(observation, dict):
            continue
        tool_name = str(observation.get("tool") or "")
        args = observation.get("args") or {}
        if _tool_category(tool_name, args) != "verification":
            continue
        return not tool_result_failed(observation.get("result"))
    return False


def answer_audit(state, answer: str) -> list[str]:
    """Check observable route requirements before accepting a final answer."""
    lowered = answer.casefold()
    failures: list[str] = []
    if re.search(r"\b(?:tests?|suite|lint|build)\b.{0,40}\bpass", lowered):
        if not _has_successful_verification(state):
            failures.append(
                "response claims verification passed without a successful check"
            )
    if getattr(state, "partial", False) and re.search(
        r"\b(?:implemented|completed|finished)\b", lowered
    ):
        failures.append("response claims completion for a partial run")
    missing_entities = [
        entity
        for entity in getattr(state, "routing_entities", [])[:30]
        if entity.casefold() not in lowered
    ]
    if missing_entities:
        failures.append("missing grounded entities: " + ", ".join(missing_entities))

    change_requested = requires_workspace_change(state.user_message)
    verification_requested = bool(_VERIFICATION_REQUEST.search(state.user_message))
    if getattr(state, "allow_write", False) and change_requested:
        if not getattr(state, "successful_mutation", False):
            failures.append("requested workspace change has not been made")
        if not getattr(state, "successful_verification", False):
            failures.append("requested verification has not completed successfully")
        mutation_paths = getattr(state, "successful_mutation_paths", set()) or set()
        if (
            mutation_paths
            and is_implementation_request(state.user_message)
            and all(is_documentation_path(path) for path in mutation_paths)
        ):
            failures.append(DOC_ONLY_MUTATION_FAILURE)

        # Real AgentState instances own these ledgers and therefore must satisfy
        # the stronger execution-proof gate. Older lightweight test/plugin state
        # objects remain compatible until they opt into the evidence contract.
        if hasattr(state, "mutation_events") and hasattr(state, "verification_events"):
            evidence = evidence_audit(state)
            if not evidence.passed:
                if evidence.missing:
                    failures.append(
                        "completion evidence missing: " + ", ".join(evidence.missing)
                    )
                if evidence.rejected:
                    failures.append(
                        "completion evidence rejected: " + ", ".join(evidence.rejected)
                    )

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
    state.task_progress = task_progress
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

    document_evidence = getattr(state, "document_evidence", {}) or {}
    if document_evidence and document_evidence.get("provenance_required"):
        sources = {
            str(record.get("source")).casefold()
            for record in document_evidence.get("records", [])
            if record.get("source")
        }
        if sources and not any(source in lowered for source in sources):
            failures.append("document provenance is not cited")
        excluded_entities = [
            entity
            for entity in document_evidence.get("excluded_entities", [])
            if len(entity) >= 5 and entity.casefold() in lowered
        ]
        if excluded_entities:
            failures.append(
                "uses entities found only in excluded document sections: "
                + ", ".join(excluded_entities[:12])
            )
    return failures
