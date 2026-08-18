import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.agent.completion import record_tool_progress
from app.agent.executor import (
    _answer_audit,
    _compact_history,
    _copies_roadmap_heading_into_source,
    _implementation_readiness_failures,
    _invalidate_read_cache_after_mutation,
    _prefetch_workspace,
    _requested_roadmap_section,
    _semantic_tool_key,
    _synthesize_partial_answer,
    _tool_result_has_evidence,
    _unchecked_roadmap_items,
    execute_plan,
    explicit_workspace_paths,
    financial_document_excerpt,
    financial_document_urls,
    financial_price_query,
    financial_research_queries,
    normalize_tool_args,
    report_pdf_link,
    requires_workspace_inspection,
    tool_result_failed,
)
from app.agent.prompts import UNTRUSTED_TOOL_RESULT_HEADER, executor_prompt
from app.core.config import AGENT_REASONING_MODEL
from app.core.permissions import FULL_WRITE, SCOPED_WRITE, PermissionPolicy
from app.tools.filesystem import current_workspace


class DummyState:
    def __init__(self):
        self.user_message = "Analyze repository"
        self.workspace = "."
        self.history = []
        self.memories = []
        self.plan = []
        self.allow_write = False
        self.model = "test-model"

        self.steps = 0
        self.finished = False
        self.observations = []

    def add_tool(self, tool, result):
        self.observations.append(
            {
                "tool": tool,
                "result": result,
            }
        )


def make_tool_call(call_id: str, name: str, arguments: dict):
    """Build a stand-in for the OpenAI SDK's tool_call object: an object
    with .id and .function.name / .function.arguments (a JSON string)."""
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def make_message(content=None, tool_calls=None):
    """Build a stand-in for the OpenAI SDK's response message object."""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def test_semantic_progress_groups_search_scope_drift_and_rejects_cached_reads():
    first = _semantic_tool_key(
        "search_code", {"pattern": "multi_expert_dispatch", "directory": "server/app"}
    )
    second = _semantic_tool_key(
        "search_code", {"pattern": "multi_expert_dispatch", "directory": "."}
    )

    assert first == second
    assert not _tool_result_has_evidence(
        "search_code", {"matches": []}, duplicate=False
    )
    assert not _tool_result_has_evidence("read_file", "existing source", duplicate=True)
    assert _tool_result_has_evidence(
        "search_code", {"matches": [{"path": "worker.py"}]}, duplicate=False
    )


def test_semantic_progress_groups_cosmetic_verification_argument_drift():
    first = _semantic_tool_key(
        "run_tests",
        {
            "kind": "pytest",
            "directory": ".",
            "test_path": "server/tests/test_planner.py",
        },
    )
    second = _semantic_tool_key(
        "run_tests",
        {
            "kind": "pytest",
            "directory": str(current_workspace()),
            "test_path": "server/tests/test_planner.py",
            "ignore_warnings": True,
            "max_chars": 100,
        },
    )

    assert first == second


def test_roadmap_heading_copy_is_not_treated_as_source_implementation():
    state = DummyState()
    state.user_message = "Implement Tier 3 from TODO.md"

    assert _copies_roadmap_heading_into_source(
        state,
        "edit_file",
        {
            "file_path": "server/app/agent/planner.py",
            "old_string": "Low-latency plan.",
            "new_string": "Low-latency plan implementing Tier 3.",
        },
    )
    assert not _copies_roadmap_heading_into_source(
        state,
        "edit_file",
        {
            "file_path": "server/app/agent/dispatch.py",
            "old_string": "def dispatch(tasks): pass",
            "new_string": "def dispatch(tasks): return run_parallel(tasks)",
        },
    )


def test_requested_roadmap_section_excludes_other_tiers_and_selects_first_item():
    contents = """# TODO

## Agent core (completed)
- [x] Streaming responses

## Tier 3 - Autonomy & scale
- [ ] Multi-expert dispatch / parallel subagents
- [ ] MCP tool ecosystem support

## Tier 4 - UX
- [ ] Interactive TUI
"""

    section = _requested_roadmap_section("Implement Tier 3 from TODO", contents)

    assert section.startswith("## Tier 3 - Autonomy & scale")
    assert "Multi-expert dispatch" in section
    assert "Streaming responses" not in section
    assert "Interactive TUI" not in section
    assert _unchecked_roadmap_items(section) == [
        "Multi-expert dispatch / parallel subagents",
        "MCP tool ecosystem support",
    ]


def test_roadmap_implementation_readiness_requires_source_symbols_and_tests():
    state = DummyState()
    state.active_roadmap_item = "Multi-expert dispatch"
    state.evidence_ledger = {
        "relevant_files": ["server/app/agent/executor.py"],
        "owning_symbols": [],
        "test_targets": [],
    }

    assert _implementation_readiness_failures(state) == [
        "owning symbols",
        "verification strategy",
    ]

    state.evidence_ledger["owning_symbols"] = [{"name": "execute_plan"}]
    state.evidence_ledger["test_targets"] = ["server/tests/test_executor.py"]
    state.evidence_ledger["verification_strategy"] = {
        "kind": "pytest",
        "test_path": "server/tests/test_executor.py",
    }
    assert _implementation_readiness_failures(state) == []


def test_mutation_cache_invalidation_is_scoped_but_drops_derived_analysis():
    cache = {
        'read_file:{"file_path": "server/app/agent/executor.py"}': "old",
        'read_file:{"file_path": "README.md"}': "keep",
        'inspect_code:{"requests": [{"path": "server/app/agent/executor.py"}]}': "old",
        'analyze_task_context:{"requirement": "dispatch"}': "derived",
        'search_code:{"directory": ".", "pattern": "execute_plan"}': "stale",
        'web_search:{"query": "current Python release"}': "keep external",
    }

    _invalidate_read_cache_after_mutation(cache, "server/app/agent/executor.py")

    assert list(cache) == [
        'read_file:{"file_path": "README.md"}',
        'web_search:{"query": "current Python release"}',
    ]


@patch("app.agent.executor.registry")
def test_coverage_edit_prefetches_real_package_baselines(mock_registry):
    state = DummyState()
    state.user_message = "Improve test coverage in this codebase"
    state.allow_write = True

    def execute(tool, _args):
        if tool == "list_files":
            return [{"path": "agents", "type": "directory"}]
        if tool == "inspect_test_environment":
            return {
                "coverage_runs": [
                    {"directory": "agents", "coverage_target": "app"},
                    {"directory": "cli", "coverage_target": "aistack_cli"},
                ]
            }
        return {"kind": "pytest_coverage", "exit_code": 0, "output": "75%"}

    mock_registry.execute.side_effect = execute
    _prefetch_workspace(
        state,
        ["list_files", "inspect_test_environment", "run_tests"],
        lambda *_args: None,
    )

    coverage_calls = [
        call
        for call in mock_registry.execute.call_args_list
        if call.args[0] == "run_tests"
    ]
    assert [call.args[1]["directory"] for call in coverage_calls] == [
        "agents",
        "cli",
    ]


@patch("app.agent.executor.registry")
def test_roadmap_prefetch_sets_exact_active_unchecked_item(mock_registry):
    state = DummyState()
    state.user_message = "Implement Tier 3 from TODO list"
    state.allow_write = True
    todo = """## Agent core (completed)
- [x] Streaming
## Tier 3 - Autonomy & scale
- [ ] Multi-expert dispatch
- [ ] MCP support
## Tier 4 - UX
- [ ] TUI
"""

    def execute(tool, args):
        if tool == "read_file" and args.get("file_path") == "TODO.md":
            return todo
        return {"status": "ok"}

    mock_registry.execute.side_effect = execute
    evidence = _prefetch_workspace(
        state,
        ["list_files", "project_summary", "tree", "inspect_files", "read_file"],
        lambda *_args: None,
    )

    assert evidence["authoritative_roadmap"].startswith("## Tier 3 - Autonomy & scale")
    assert "Streaming" not in evidence["authoritative_roadmap"]
    assert "TUI" not in evidence["authoritative_roadmap"]
    assert state.roadmap_requirements == ["Multi-expert dispatch", "MCP support"]
    assert state.active_roadmap_item == "Multi-expert dispatch"


@pytest.mark.parametrize(
    ("result", "failed"),
    [
        ({"error": "bad arguments"}, True),
        ({"tool_error": "dependency missing"}, True),
        ({"exit_code": 4, "output": "unrecognized arguments: --cov"}, True),
        ({"items": [{"path": "missing", "error": "Not found"}]}, True),
        (
            {
                "items": [
                    {"path": "README.md", "content": "ok"},
                    {"path": "missing", "error": "Not found"},
                ]
            },
            False,
        ),
        ({"exit_code": 0, "output": "passed"}, False),
        ({"status": "edited"}, False),
    ],
)
def test_tool_result_failed_recognizes_all_tool_failure_shapes(result, failed):
    assert tool_result_failed(result) is failed


def test_answer_audit_checks_entities_deliverables_and_document_provenance():
    state = DummyState()
    state.routing_entities = ["Contract Alpha"]
    state.route_deliverables = ["Risk matrix"]
    state.document_evidence = {
        "provenance_required": True,
        "records": [{"source": "report.pdf"}],
    }

    failures = _answer_audit(state, "Contract Alpha has material exposure.")

    assert "deliverable may be missing: Risk matrix" in failures
    assert "document provenance is not cited" in failures
    assert (
        _answer_audit(
            state,
            "Contract Alpha appears in report.pdf. Risk matrix: medium likelihood.",
        )
        == []
    )


def test_answer_audit_rejects_entities_from_excluded_document_sections():
    state = DummyState()
    state.routing_entities = []
    state.route_deliverables = []
    state.document_evidence = {
        "provenance_required": True,
        "records": [{"source": "report.pdf", "text": "CurrentCo"}],
        "excluded_entities": ["Historical Example Limited BO"],
    }

    failures = _answer_audit(
        state,
        "According to report.pdf, Historical Example Limited BO is a current holding.",
    )

    assert "uses entities found only in excluded document sections" in failures[0]


def test_answer_audit_enforces_mutation_verification_and_task_contract():
    state = DummyState()
    state.allow_write = True
    state.user_message = "Improve test coverage and verify the tests pass"
    state.successful_mutation = False
    state.successful_verification = False
    state.pending_failure_categories = {"verification"}
    state.route_tasks = [
        {
            "id": "improve_coverage",
            "completion_criteria": [
                "Update tests",
                "Verify the test suite passes",
            ],
        }
    ]
    state.task_progress = {}

    failures = _answer_audit(state, "I will run coverage next.")

    assert "requested workspace change has not been made" in failures
    assert "requested verification has not completed successfully" in failures
    assert "unresolved failed tool categories: verification" in failures
    assert "task completion criteria not met: improve_coverage" in failures
    assert state.task_progress["improve_coverage"] == "pending"

    state.successful_mutation = True
    state.successful_verification = True
    state.pending_failure_categories.clear()

    assert _answer_audit(state, "Updated tests; the test suite passes.") == []
    assert state.task_progress["improve_coverage"] == "completed"


def test_answer_audit_accepts_substantive_synthesis_report():
    state = DummyState()
    state.route_tasks = [
        {
            "id": "synthesize_report",
            "completion_criteria": ["Synthesize a coverage report and recommendations"],
        }
    ]
    state.observations = [{"tool": "run_tests", "result": {"exit_code": 0}}]
    answer = (
        "Coverage improved after adding the focused regression test. The verified suite "
        "passes, and the next recommendation is to cover the remaining error branches."
    )

    assert _answer_audit(state, answer) == []
    assert state.task_progress["synthesize_report"] == "completed"


def test_answer_audit_requires_verification_after_any_mutation():
    state = DummyState()
    state.allow_write = True
    # "add" triggers the change policy; no verification keyword is present.
    state.user_message = "Add a helper function"
    state.successful_mutation = True
    state.successful_verification = False

    failures = _answer_audit(state, "Added the helper function.")

    assert "requested verification has not completed successfully" in failures
    assert "requested workspace change has not been made" not in failures

    state.successful_verification = True
    assert _answer_audit(state, "Added the helper function; tests pass.") == []


def test_answer_audit_rejects_unsupported_pass_and_partial_completion_claims():
    state = DummyState()
    state.successful_verification = False
    state.partial = True

    failures = _answer_audit(
        state, "Implemented the feature and the test suite passes."
    )

    assert "response claims verification passed without a successful check" in failures
    assert "response claims completion for a partial run" in failures


def test_record_tool_progress_tracks_mutated_paths():
    state = DummyState()

    record_tool_progress(
        state,
        "write_file",
        {"file_path": "./server/app/worker.py"},
        {"status": "written"},
    )

    assert state.successful_mutation is True
    assert "server/app/worker.py" in state.successful_mutation_paths


def test_record_tool_progress_treats_apply_patch_as_mutation():
    state = DummyState()

    record_tool_progress(
        state,
        "apply_patch",
        {"file_path": "server/app/worker.py", "old_string": "old", "new_string": "new"},
        {"status": "edited"},
    )

    assert state.successful_mutation is True
    assert state.successful_mutation_paths == {"server/app/worker.py"}


def test_mutation_invalidates_earlier_verification():
    state = DummyState()
    state.successful_verification = True

    record_tool_progress(
        state,
        "edit_file",
        {"file_path": "server/app/worker.py"},
        {"status": "edited"},
    )

    assert state.successful_mutation is True
    assert state.successful_verification is False

    record_tool_progress(
        state,
        "run_tests",
        {"kind": "pytest", "test_path": "tests/test_worker.py"},
        {"exit_code": 0, "output": "1 passed"},
    )
    assert state.successful_verification is True


def test_answer_audit_rejects_todo_only_mutation_for_implementation():
    state = DummyState()
    state.allow_write = True
    state.user_message = "Review this project and from TODO list implement Tier 3"
    state.successful_mutation = True
    state.successful_verification = True
    state.successful_mutation_paths = {"TODO.md"}

    failures = _answer_audit(state, "Implemented Tier 3.")

    assert any(
        "only modified documentation/marker files" in failure for failure in failures
    )

    state.successful_mutation_paths = {"TODO.md", "server/app/executor.py"}
    assert _answer_audit(state, "Implemented Tier 3.") == []


def test_answer_audit_allows_documentation_request_with_md_only_change():
    state = DummyState()
    state.allow_write = True
    state.user_message = "Update the README with usage examples"
    state.successful_mutation = True
    state.successful_verification = True
    state.successful_mutation_paths = {"README.md"}

    assert _answer_audit(state, "Updated the README.") == []


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_forces_verification_before_final_answer_after_edit(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.allow_write = True
    state.user_message = "Add a helper function"  # no verify keyword in request
    mock_registry.list_tools.return_value = ["write_file", "edit_file", "run_tests"]
    mock_registry.execute.side_effect = [
        {"status": "written", "path": "helpers.py"},
        {"exit_code": 0, "output": "All checks passed!"},
        {"exit_code": 0, "output": "1 passed"},
    ]
    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[
                make_tool_call(
                    "write",
                    "write_file",
                    {"file_path": "helpers.py", "content": "def helper(): pass\n"},
                )
            ]
        ),
        make_message(content="Done. Added the helper function."),
        make_message(
            tool_calls=[make_tool_call("test", "run_tests", {"kind": "pytest"})]
        ),
        make_message(content="Added the helper function; the test suite passes."),
    ]

    with patch("app.agent.executor.SANDBOX_ROOT", current_workspace().parent):
        assert (
            execute_plan(state) == "Added the helper function; the test suite passes."
        )
    assert mock_registry.execute.call_count == 3
    assert mock_registry.execute.call_args_list[1].args == (
        "run_tests",
        {"kind": "ruff", "directory": ".", "test_path": "helpers.py"},
    )
    transcript = mock_chat_with_tools.call_args_list[1].args[0]
    repair_messages = [
        message["content"]
        for message in transcript
        if message["role"] == "user" and "read the failure output" in message["content"]
    ]
    assert (
        repair_messages
    ), "draft without verification must be rejected with a repair loop"


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_guides_search_after_hallucinated_edit_anchor(
    mock_chat_with_tools, mock_registry
):
    """A failed edit anchor must trigger targeted search guidance, not a
    generic tool-failure nudge that lets the model retry the same bad edit."""
    state = DummyState()
    state.allow_write = True
    state.user_message = "Implement parallel subagents in the runner"
    mock_registry.list_tools.return_value = [
        "edit_file",
        "search_text",
        "read_file",
        "run_tests",
    ]
    mock_registry.execute.side_effect = [
        {"error": "old_string not found in file. Re-read the file and try again."},
        [{"path": "server/app/runner.py"}],
        {"path": "server/app/runner.py", "status": "edited"},
        {"kind": "pytest", "exit_code": 0, "output": "1 passed"},
    ]
    seen_messages = []
    events = []

    def respond(messages, **kwargs):
        seen_messages.append([dict(message) for message in messages])
        if len(seen_messages) == 1:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "edit",
                        "edit_file",
                        {
                            "file_path": "server/app/runner.py",
                            "old_string": "def execute(self):\n    return []",
                            "new_string": "def execute(self):\n    return self._dispatch()",
                        },
                    )
                ]
            )
        if len(seen_messages) == 2:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "search",
                        "search_text",
                        {"keyword": "def execute", "directory": "server/app"},
                    )
                ]
            )
        if len(seen_messages) == 3:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "edit",
                        "edit_file",
                        {
                            "file_path": "server/app/runner.py",
                            "old_string": "def execute(request: ExecuteRequest, x_runner_key: str | None = Header(None)):",
                            "new_string": "def execute(request: ExecuteRequest, x_runner_key: str | None = Header(None)):\n    # Dispatch work through the runner.",
                        },
                    )
                ]
            )
        if len(seen_messages) == 4:
            return make_message(
                tool_calls=[make_tool_call("test", "run_tests", {"kind": "pytest"})]
            )
        return make_message(
            content="Implemented parallel subagents in the runner; tests pass."
        )

    mock_chat_with_tools.side_effect = respond

    assert (
        execute_plan(
            state, on_event=lambda kind, payload: events.append((kind, payload))
        )
        == "Implemented parallel subagents in the runner; tests pass."
    )

    guidance = [
        message["content"]
        for message in seen_messages[1]
        if message["role"] == "user" and "old_string" in message["content"]
    ]
    assert guidance, "failed edit anchor must produce targeted recovery guidance"
    assert "search_text or search_code" in guidance[0]
    assert "Do not call the same edit again" in guidance[0]
    assert state.successful_mutation is True
    assert state.successful_verification is True
    assert not any(kind == "unproductive_tool_loop" for kind, _ in events)


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_corrects_absolute_sandbox_path(
    mock_chat_with_tools, mock_registry
):
    """A path denied because it escapes the workspace must trigger path
    guidance (workspace-relative paths), not the edit-anchor guidance."""
    state = DummyState()
    state.allow_write = True
    state.user_message = "Implement parallel subagents in the runner"
    mock_registry.list_tools.return_value = [
        "edit_file",
        "write_file",
        "read_file",
        "run_tests",
    ]
    mock_registry.execute.side_effect = [
        {"error": "Access outside workspace denied."},
        {"error": "Access outside workspace denied."},
        {"path": "server/app/runner.py", "status": "edited"},
        {"kind": "pytest", "exit_code": 0, "output": "1 passed"},
    ]
    seen_messages = []
    events = []

    def respond(messages, **kwargs):
        seen_messages.append([dict(message) for message in messages])
        if len(seen_messages) == 1:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "edit",
                        "edit_file",
                        {
                            "file_path": "/sandbox/server/app/runner.py",
                            "old_string": "def _authorize(x):\n    return True",
                            "new_string": "def _authorize(x):\n    return True\n\ndef run_subagents():\n    return []",
                        },
                    )
                ]
            )
        if len(seen_messages) == 2:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "write",
                        "write_file",
                        {
                            "file_path": "/sandbox/server/app/runner.py",
                            "content": "def run_subagents():\n    return []",
                        },
                    )
                ]
            )
        if len(seen_messages) == 3:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "edit",
                        "edit_file",
                        {
                            "file_path": "server/app/runner.py",
                            "old_string": "def _authorize(x):\n    return True",
                            "new_string": "def _authorize(x):\n    return True\n\ndef run_subagents():\n    return []",
                        },
                    )
                ]
            )
        if len(seen_messages) == 4:
            return make_message(
                tool_calls=[make_tool_call("test", "run_tests", {"kind": "pytest"})]
            )
        return make_message(
            content="Implemented parallel subagents in the runner; tests pass."
        )

    mock_chat_with_tools.side_effect = respond

    assert (
        execute_plan(
            state, on_event=lambda kind, payload: events.append((kind, payload))
        )
        == "Implemented parallel subagents in the runner; tests pass."
    )

    path_guidance = [
        message["content"]
        for message in seen_messages[1]
        if message["role"] == "user" and "workspace-relative" in message["content"]
    ]
    repeated_guidance = [
        message["content"]
        for message in seen_messages[2]
        if message["role"] == "user" and "workspace-relative" in message["content"]
    ]
    assert path_guidance, "path denial must produce workspace-relative guidance"
    assert "absolute path" in path_guidance[0]
    assert (
        "repeated" in repeated_guidance[-1] or "more than once" in repeated_guidance[-1]
    )
    assert state.successful_mutation is True
    assert state.successful_verification is True
    assert not any(kind == "unproductive_tool_loop" for kind, _ in events)
    assert sum(1 for kind, _ in events if kind == "path_denial_recovery") >= 2


@patch("app.agent.executor.dispatch_experts")
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_injects_expert_findings_when_dispatch_requested(
    mock_chat_with_tools, mock_registry, mock_dispatch
):
    """A request marked for expert dispatch runs the bounded expert analyses
    before the tool loop and merges their structured findings into the task
    context and evidence ledger."""
    canned = [
        {
            "expert": "architecture",
            "findings": [
                {
                    "claim": "runner owns the parallel loop",
                    "evidence": ["server/app/runner.py"],
                    "confidence": "high",
                }
            ],
            "open_questions": [],
            "recommended_focus": ["server/app/runner.py"],
        }
    ]
    mock_dispatch.return_value = canned
    state = DummyState()
    state.allow_write = True
    state.expert_dispatch = True
    state.user_message = "Implement parallel subagents in the runner"
    state.evidence_ledger = {
        "requirements": [],
        "relevant_files": [],
        "owning_symbols": [],
        "test_targets": [],
        "dependencies": {},
        "confirmed_existing": [],
        "confirmed_missing": [],
        "open_questions": [],
    }
    mock_registry.list_tools.return_value = [
        "edit_file",
        "write_file",
        "read_file",
        "run_tests",
    ]
    mock_registry.execute.side_effect = [
        {"path": "server/app/runner.py", "status": "edited"},
        {"kind": "pytest", "exit_code": 0, "output": "1 passed"},
    ]
    seen_messages = []

    def respond(messages, **kwargs):
        seen_messages.append([dict(message) for message in messages])
        if len(seen_messages) == 1:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "edit",
                        "edit_file",
                        {
                            "file_path": "server/app/runner.py",
                            "old_string": "def _loop():\n    pass",
                            "new_string": "def _loop():\n    return dispatch()",
                        },
                    )
                ]
            )
        if len(seen_messages) == 2:
            return make_message(
                tool_calls=[make_tool_call("test", "run_tests", {"kind": "pytest"})]
            )
        return make_message(content="Implemented parallel subagents; tests pass.")

    mock_chat_with_tools.side_effect = respond

    assert execute_plan(state) == "Implemented parallel subagents; tests pass."

    mock_dispatch.assert_called_once()
    assert state.expert_findings == canned
    assert state.evidence_ledger["expert_findings"] == canned
    first_user = seen_messages[0][1]["content"]
    assert "Multi-expert structured findings" in first_user
    assert "runner owns the parallel loop" in first_user


@patch("app.agent.executor.dispatch_experts")
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_skips_expert_dispatch_without_marker(
    mock_chat_with_tools, mock_registry, mock_dispatch
):
    """Ordinary requests never launch expert analyses; the marker must be set
    explicitly by the service layer, so existing fast loops stay unchanged."""
    state = DummyState()
    state.allow_write = True
    state.user_message = "Implement parallel subagents in the runner"
    mock_registry.list_tools.return_value = [
        "edit_file",
        "write_file",
        "read_file",
        "run_tests",
    ]
    mock_registry.execute.side_effect = [
        {"path": "server/app/runner.py", "status": "edited"},
        {"kind": "pytest", "exit_code": 0, "output": "1 passed"},
    ]
    seen_messages = []

    def respond(messages, **kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "edit",
                        "edit_file",
                        {
                            "file_path": "server/app/runner.py",
                            "old_string": "old",
                            "new_string": "new",
                        },
                    )
                ]
            )
        if len(seen_messages) == 2:
            return make_message(
                tool_calls=[make_tool_call("test", "run_tests", {"kind": "pytest"})]
            )
        return make_message(content="Implemented parallel subagents; tests pass.")

    mock_chat_with_tools.side_effect = respond

    assert execute_plan(state) == "Implemented parallel subagents; tests pass."

    mock_dispatch.assert_not_called()
    assert getattr(state, "expert_findings", []) == []
    assert not any(
        "Multi-expert structured findings" in message["content"]
        for message in seen_messages[0]
    )


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_refuses_doc_shortcut_before_it_changes_workspace(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.allow_write = True
    state.user_message = "Review this project and from TODO list implement Tier 3"
    mock_registry.list_tools.return_value = ["write_file", "run_tests"]
    mock_registry.execute.return_value = {"exit_code": 0, "output": "1 passed"}
    todo_write = make_tool_call(
        "write", "write_file", {"file_path": "TODO.md", "content": "- [x] item\n"}
    )
    mock_chat_with_tools.side_effect = [
        make_message(tool_calls=[todo_write]),
        make_message(content="Implemented Tier 3."),
        make_message(
            tool_calls=[make_tool_call("test", "run_tests", {"kind": "pytest"})]
        ),
        make_message(content="Implemented Tier 3."),
        make_message(tool_calls=[todo_write]),
        make_message(content="Implemented Tier 3."),
        make_message(tool_calls=[todo_write]),
        make_message(content="Implemented Tier 3."),
    ]

    result = execute_plan(state)

    assert "Incomplete requirements:" in result
    assert "requested workspace change has not been made" in result
    assert getattr(state, "successful_mutation", False) is False
    assert not any(
        call.args[0] == "write_file" for call in mock_registry.execute.call_args_list
    )


@patch("app.agent.executor.MAX_EMPTY_MODEL_TURNS", 1)
@patch("app.agent.executor.AGENT_MODEL_ESCALATIONS", 0)
@patch(
    "app.agent.executor._synthesize_partial_answer", return_value="Partial synthesis."
)
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_partial_finalize_after_refused_doc_shortcut_has_no_diff(
    mock_chat_with_tools, mock_registry, mock_synthesize
):
    state = DummyState()
    state.allow_write = True
    state.user_message = "Review this project and implement Tier 3"
    mock_registry.list_tools.return_value = ["write_file"]
    mock_registry.execute.side_effect = [
        {"status": "written", "path": "TODO.md"},
    ]
    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[
                make_tool_call(
                    "write",
                    "write_file",
                    {"file_path": "TODO.md", "content": "- [x]\n"},
                )
            ]
        ),
        make_message(content=""),
    ]

    result = execute_plan(state)

    assert getattr(state, "diff_blocked", False) is False
    assert "Incomplete requirements:" in result
    assert "requested workspace change has not been made" in result


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_empty_turn_after_anchor_recovery_escalates_and_finishes_change(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.allow_write = True
    state.user_message = "Implement the missing feature in this codebase"
    mock_registry.list_tools.return_value = [
        "edit_file",
        "search_code",
        "read_file",
        "run_tests",
    ]
    mock_registry.execute.side_effect = [
        {"error": "old_string not found in file. Re-read the file."},
        {
            "matches": [{"path": "app.py", "line": 1, "text": "def execute(state):"}],
            "truncated": False,
        },
        "def execute(state):\n    return False\n",
        {"status": "edited", "path": "app.py"},
        {"exit_code": 0, "output": "1 passed"},
    ]
    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[
                make_tool_call(
                    "bad-edit",
                    "edit_file",
                    {
                        "file_path": "app.py",
                        "old_string": "def execute(self):\n    return False",
                        "new_string": "def execute(self):\n    return True",
                    },
                )
            ]
        ),
        make_message(
            tool_calls=[
                make_tool_call(
                    "search",
                    "search_code",
                    {"directory": "app.py", "pattern": "execute"},
                )
            ]
        ),
        make_message(
            tool_calls=[
                make_tool_call(
                    "read",
                    "read_file",
                    {"file_path": "app.py", "start_line": 1, "end_line": 2},
                )
            ]
        ),
        make_message(content=""),
        make_message(
            tool_calls=[
                make_tool_call(
                    "good-edit",
                    "edit_file",
                    {
                        "file_path": "app.py",
                        "old_string": "def execute(state):\n    return False",
                        "new_string": "def execute(state):\n    return True",
                    },
                )
            ]
        ),
        make_message(
            tool_calls=[make_tool_call("tests", "run_tests", {"kind": "pytest"})]
        ),
        make_message(content="Implemented the feature; tests pass."),
    ]
    events = []

    result = execute_plan(
        state, on_event=lambda kind, payload: events.append((kind, payload))
    )

    assert result == "Implemented the feature; tests pass."
    assert state.successful_mutation is True
    assert state.successful_verification is True
    escalations = [payload for kind, payload in events if kind == "model_escalated"]
    assert escalations == [
        {
            "from": "test-model",
            "to": AGENT_REASONING_MODEL,
            "reason": "empty model turn during required implementation",
            "escalation": 1,
        }
    ]
    escalation_call = mock_chat_with_tools.call_args_list[4]
    assert escalation_call.kwargs["model"] == AGENT_REASONING_MODEL
    handoff = escalation_call.args[0][2]["content"]
    assert "def execute(state)" in handoff
    assert "old_string not found" in handoff


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_repairs_failing_verification_before_answering(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.allow_write = True
    state.user_message = "Fix the broken test"
    mock_registry.list_tools.return_value = ["write_file", "edit_file", "run_tests"]
    mock_registry.execute.side_effect = [
        {"status": "written", "path": "test_broken.py"},
        {"kind": "pytest", "exit_code": 1, "output": "FAILED test_broken.py::test_x"},
        {"status": "edited", "path": "test_broken.py"},
        {"kind": "pytest", "exit_code": 0, "output": "1 passed"},
    ]
    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[
                make_tool_call(
                    "write",
                    "write_file",
                    {"file_path": "test_broken.py", "content": "test"},
                )
            ]
        ),
        make_message(
            tool_calls=[make_tool_call("test", "run_tests", {"kind": "pytest"})]
        ),
        make_message(content="I could not fix it; the tests still fail."),
        make_message(
            tool_calls=[
                make_tool_call(
                    "repair",
                    "edit_file",
                    {
                        "file_path": "test_broken.py",
                        "old_string": "test",
                        "new_string": "def test_x(): assert True\n",
                    },
                )
            ]
        ),
        make_message(
            tool_calls=[make_tool_call("retest", "run_tests", {"kind": "pytest"})]
        ),
        make_message(content="The failing test was repaired; the suite passes."),
    ]

    assert execute_plan(state) == "The failing test was repaired; the suite passes."
    assert mock_registry.execute.call_count == 4
    assert state.successful_mutation is True
    assert state.successful_verification is True


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_reflects_before_accepting_unevidenced_answer(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    mock_registry.list_tools.return_value = []
    long_guess = (
        "This repository uses a FastAPI backend with PostgreSQL and Redis, and it "
        "serves a React dashboard to authenticated users through a WebSocket API. "
        "The CLI is packaged with Poetry and the UI uses a PostgreSQL queue. "
    )
    mock_chat_with_tools.side_effect = [
        make_message(content=long_guess),
        make_message(content=long_guess),
    ]
    events = []

    result = execute_plan(
        state, on_event=lambda kind, payload: events.append((kind, payload))
    )

    assert result == long_guess.strip()
    assert [kind for kind, _ in events].count("reflection_required") == 1
    transcript = mock_chat_with_tools.call_args_list[1].args[0]
    assert any(
        message["role"] == "user"
        and "reviewing your previous reasoning" in message["content"]
        for message in transcript
    )


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_reflection_runs_at_most_once(mock_chat_with_tools, mock_registry):
    state = DummyState()
    mock_registry.list_tools.return_value = []
    long_guess = (
        "This repository implements a distributed event pipeline with Kafka-backed "
        "streams and a gRPC control plane that coordinates worker replicas. "
    )
    mock_chat_with_tools.return_value = make_message(content=long_guess)
    events = []

    result = execute_plan(
        state, on_event=lambda kind, payload: events.append((kind, payload))
    )

    assert result == long_guess.strip()
    assert mock_chat_with_tools.call_count == 2
    assert [kind for kind, _ in events].count("reflection_required") == 1


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_retries_transient_tool_error_once(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.user_message = "Fetch the current contents of this URL"
    mock_registry.list_tools.return_value = ["web_fetch"]
    mock_registry.execute.side_effect = [
        {"tool_error": "Connection reset by peer"},
        {"text": "fetched content"},
    ]
    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[make_tool_call("fetch", "web_fetch", {"url": "https://x.test"})]
        ),
        make_message(content="Done"),
    ]
    events = []

    assert (
        execute_plan(
            state,
            on_event=lambda kind, payload: events.append((kind, payload)),
            force_research=True,
        )
        == "Done"
    )
    assert mock_registry.execute.call_count == 2
    assert [kind for kind, _ in events].count("tool_retry") == 1
    assert state.observations[-1]["result"] == {"text": "fetched content"}


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_does_not_retry_deterministic_tool_failure(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.user_message = "Fetch the current contents of this URL"
    mock_registry.list_tools.return_value = ["web_fetch"]
    mock_registry.execute.return_value = {"error": "URL returned HTTP 404"}
    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[make_tool_call("fetch", "web_fetch", {"url": "https://x.test"})]
        ),
        make_message(content="Done"),
    ]
    events = []

    assert (
        execute_plan(
            state,
            on_event=lambda kind, payload: events.append((kind, payload)),
            force_research=True,
        )
        == "Done"
    )
    mock_registry.execute.assert_called_once()
    assert not any(kind == "tool_retry" for kind, _ in events)


def test_failed_guess_does_not_invalidate_successful_inspection():
    state = DummyState()

    record_tool_progress(state, "list_files", {"directory": "."}, ["server/tests"])
    record_tool_progress(
        state,
        "list_files",
        {"directory": "tests"},
        {"error": "directory not found"},
    )

    assert state.pending_failure_categories == set()


@pytest.fixture(autouse=True)
def no_real_compaction():
    """Executor tests never run long enough to need real compaction, and
    letting it run for real would call the live chat() completion. Keep the
    history untouched instead."""
    with patch(
        "app.agent.executor._compact_history",
        side_effect=lambda messages, model: messages,
    ):
        yield


# ----------------------------------------------------
# normalize_tool_args
# ----------------------------------------------------


def test_normalize_tool_args_read_file():
    args = {"path": "README.md"}

    result = normalize_tool_args("read_file", args)

    assert result == {"file_path": "README.md"}


def test_context_compaction_is_deterministic_and_preserves_tool_evidence():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "implement feature"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "read",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"file_path":"app.py"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "read_file",
            "content": "def execute(): return 1",
        },
        {"role": "user", "content": "repair the failing implementation"},
        {"role": "assistant", "content": "working"},
        {"role": "user", "content": "verify next"},
        {"role": "assistant", "content": "running verification"},
        {"role": "user", "content": "continue"},
    ]

    compacted = _compact_history(messages, "unused-model", goal="implement feature")

    assert len(compacted) < len(messages)
    assert "read_file" in compacted[2]["content"]
    assert "app.py" in compacted[2]["content"]
    assert "def execute" in compacted[2]["content"]


def test_normalize_tool_args_tree():
    args = {"path": "."}

    result = normalize_tool_args("tree", args)

    assert result == {"directory": "."}


def test_normalize_tool_args_no_change():
    args = {"directory": "."}

    result = normalize_tool_args("tree", args)

    assert result == {"directory": "."}


def test_financial_price_query_keeps_company_and_market():
    assert (
        financial_price_query(
            "search renata last closing day price from DSE also analyze it in five years"
        )
        == "renata DSE latest closing price previous close historical data"
    )


def test_financial_research_queries_cover_company_and_sector_evidence():
    queries = financial_research_queries(
        "search renata last closing day price from DSE"
    )

    assert len(queries) == 4
    assert "renata DSE" in queries[0]
    assert "annual report" in queries[1]
    assert "latest company news" in queries[2]
    assert "pharmaceutical healthcare sector" in queries[3]


def test_financial_document_urls_prefer_filing_pdf_and_company_news():
    searches = [
        {},
        {
            "results": [
                {"url": "https://company.test/report"},
                {"url": "https://company.test/report.pdf"},
            ]
        },
        {"results": [{"url": "https://news.test/company"}]},
    ]

    assert financial_document_urls(searches) == [
        "https://company.test/report.pdf",
        "https://news.test/company",
    ]


def test_financial_document_urls_reject_unrelated_pdf():
    searches = [
        {},
        {
            "query": "Renata annual report",
            "results": [
                {"title": "Renata annual report", "url": "https://renata.test/archive"},
                {
                    "title": "Other company annual report",
                    "url": "https://other.test/report.pdf",
                },
            ],
        },
        {"results": [{"title": "Renata update", "url": "https://news.test/renata"}]},
    ]

    assert financial_document_urls(searches) == [
        "https://renata.test/archive",
        "https://news.test/renata",
    ]


def test_report_pdf_link_uses_annual_report_pdf_only():
    assert report_pdf_link(
        {
            "links": [
                "https://company.test/about.pdf",
                "https://company.test/annual-report.pdf",
            ]
        }
    ) == ("https://company.test/annual-report.pdf")


def test_financial_document_excerpt_keeps_end_of_report():
    text = "start" + ("x" * 4_000) + "audited statement"

    excerpt = financial_document_excerpt(text, limit=1_000)

    assert excerpt.startswith("start")
    assert excerpt.endswith("audited statement")
    assert "middle omitted" in excerpt


@pytest.mark.parametrize(
    ("mode", "research", "expected"),
    [
        ("quick", False, "fast personal assistant"),
        ("code", False, "software engineering agent"),
        ("research", True, "web-research agent"),
        ("finance", True, "finance research request"),
        ("deep", False, "careful analysis agent"),
        ("vision", False, "image-aware assistant"),
    ],
)
def test_executor_prompt_selects_profile_policy(mode, research, expected):
    assert expected in executor_prompt(mode, research)


# ----------------------------------------------------
# execute_plan
# ----------------------------------------------------


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_returns_final_answer(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    mock_chat_with_tools.return_value = make_message(content="Done", tool_calls=None)

    result = execute_plan(state)

    assert result == "Done"
    assert state.finished is True


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_executes_tool(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = ["list_files"]
    mock_registry.execute.return_value = {"files": ["README.md"]}

    mock_chat_with_tools.side_effect = [
        make_message(
            content=None,
            tool_calls=[make_tool_call("call_1", "list_files", {"directory": "."})],
        ),
        make_message(content="Finished", tool_calls=None),
    ]

    result = execute_plan(state)

    assert result == "Finished"

    mock_registry.execute.assert_called_once_with(
        "list_files",
        {"directory": "."},
    )

    assert state.observations[0]["tool"] == "list_files"
    assert state.observations[0]["result"] == {"files": ["README.md"]}


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_emits_permissions_event_and_marks_results_untrusted(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()
    state.allow_write = False

    mock_registry.list_tools.return_value = ["list_files"]
    mock_registry.execute.return_value = {"files": ["README.md"]}

    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[make_tool_call("call_1", "list_files", {"directory": "."})]
        ),
        make_message(content="Finished", tool_calls=None),
    ]
    events = []

    execute_plan(state, on_event=lambda kind, payload: events.append((kind, payload)))

    permissions_events = [payload for kind, payload in events if kind == "permissions"]
    assert len(permissions_events) == 1
    assert permissions_events[0]["scope"] == "read"
    assert permissions_events[0]["allow_write"] is False

    messages_for_final_call = mock_chat_with_tools.call_args_list[1].args[0]
    tool_messages = [m for m in messages_for_final_call if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"].startswith(UNTRUSTED_TOOL_RESULT_HEADER)


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_prefers_state_permissions_over_allow_write(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()
    state.allow_write = False
    state.permissions = PermissionPolicy(
        scope=SCOPED_WRITE,
        edit_roots=(Path("src"),),
        command_allowlist=frozenset({"pytest"}),
    )

    mock_registry.list_tools.return_value = []
    mock_chat_with_tools.return_value = make_message(content="Done", tool_calls=None)
    events = []

    execute_plan(state, on_event=lambda kind, payload: events.append((kind, payload)))

    permissions_events = [payload for kind, payload in events if kind == "permissions"]
    assert permissions_events[0]["scope"] == "scoped-write"
    assert permissions_events[0]["edit_roots"] == ["src"]
    assert permissions_events[0]["command_allowlist"] == ["pytest"]
    assert permissions_events[0]["allow_write"] is False


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_full_write_policy_from_allow_write(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.allow_write = True

    mock_registry.list_tools.return_value = []
    mock_chat_with_tools.return_value = make_message(content="Done", tool_calls=None)
    events = []

    execute_plan(state, on_event=lambda kind, payload: events.append((kind, payload)))

    permissions_events = [payload for kind, payload in events if kind == "permissions"]
    assert permissions_events[0]["scope"] == FULL_WRITE


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_workspace_code_task_hides_web_tools_without_research_requirement(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.user_message = "Review this repository and explain the worker"
    mock_registry.list_tools.return_value = [
        "list_files",
        "read_file",
        "web_search",
        "web_fetch",
    ]
    mock_registry.execute.return_value = {"files": ["worker.py"]}
    mock_chat_with_tools.return_value = make_message(content="Reviewed the worker.")

    assert execute_plan(state) == "Reviewed the worker."

    advertised = {
        schema["function"]["name"]
        for schema in mock_chat_with_tools.call_args.kwargs["tools"]
    }
    assert advertised == {"list_files", "read_file"}


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_runner_timeout_gets_its_own_durable_event(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()
    state.allow_write = True
    mock_registry.list_tools.return_value = ["run_tests"]
    mock_registry.execute.return_value = {
        "job_id": "job-1",
        "status": "timed_out",
        "exit_code": 124,
    }
    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[make_tool_call("call-1", "run_tests", {"kind": "pytest"})]
        ),
        make_message(content="Tests timed out."),
    ]
    events = []

    assert execute_plan(
        state, on_event=lambda kind, payload: events.append((kind, payload))
    )

    assert (
        "tool_timed_out",
        {"tool": "run_tests", "job_id": "job-1", "exit_code": 124},
    ) in events


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_checkpoints_tool_progress(mock_chat_with_tools, mock_registry):
    state = DummyState()
    mock_registry.list_tools.return_value = ["list_files"]
    mock_registry.execute.return_value = {"files": ["README.md"]}
    mock_chat_with_tools.side_effect = [
        make_message(
            tool_calls=[make_tool_call("call_1", "list_files", {"directory": "."})]
        ),
        make_message(content="Done"),
    ]
    checkpoints = []

    assert execute_plan(state, on_checkpoint=checkpoints.append) == "Done"
    assert len(checkpoints) == 1
    checkpoint = checkpoints[0]
    assert checkpoint["steps"] == 1
    assert checkpoint["plan"] == []
    assert checkpoint["observations"] == [
        {"tool": "list_files", "result": {"files": ["README.md"]}}
    ]
    assert checkpoint["route_tasks"] == []
    assert checkpoint["task_progress"] == {}
    assert checkpoint["successful_mutation"] is False
    assert checkpoint["successful_verification"] is False
    assert checkpoint["pending_failure_categories"] == []
    transcript = checkpoint["messages"]
    assert [message["role"] for message in transcript] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert transcript[-1]["tool_call_id"] == "call_1"


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_resumes_from_restored_transcript(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.steps = 1
    state.restored_transcript = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "list_files",
                        "arguments": '{"directory": "."}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": '[{"path": "README.md"}]',
        },
    ]
    mock_registry.list_tools.return_value = ["list_files"]
    mock_chat_with_tools.side_effect = [
        make_message(content="Continuing from the prior evidence.", tool_calls=None),
    ]
    events = []

    assert (
        execute_plan(
            state, on_event=lambda kind, payload: events.append((kind, payload))
        )
        == "Continuing from the prior evidence."
    )
    assert state.restored_transcript == []
    transcript = mock_chat_with_tools.call_args.args[0]
    assert len(transcript) == 4  # system + task + two restored messages
    assert transcript[2]["tool_calls"][0]["id"] == "call_1"
    assert transcript[3]["tool_call_id"] == "call_1"


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_prefetches_current_external_information(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.user_message = "Search Renata last closing price on DSE today"
    mock_registry.list_tools.return_value = ["web_search"]
    mock_registry.execute.return_value = {
        "results": [{"title": "RENATA", "url": "https://example.test/renata"}]
    }
    mock_chat_with_tools.return_value = make_message(content="Result", tool_calls=None)

    result = execute_plan(state)

    assert result == "Result"
    assert mock_registry.execute.call_args_list[0].args[0] == "web_search"
    assert mock_registry.execute.call_args_list[0].args[1]["query"] == (
        "Renata DSE latest closing price previous close historical data"
    )
    assert (
        "https://example.test/renata"
        in mock_chat_with_tools.call_args.args[0][1]["content"]
    )
    tools = mock_chat_with_tools.call_args.kwargs["tools"]
    assert [tool["function"]["name"] for tool in tools] == ["web_search"]


def test_requires_workspace_inspection_recognizes_repo_shorthand():
    assert requires_workspace_inspection("Review this repo") is True
    assert requires_workspace_inspection("Inspect only README.md") is True
    assert requires_workspace_inspection("Find today's market price") is False


def test_requires_workspace_inspection_recognizes_project_and_todo_wording():
    assert (
        requires_workspace_inspection(
            "Review this project and from TODO list implement Tier 3"
        )
        is True
    )
    assert requires_workspace_inspection("Implement the items in TODO.md") is True
    assert requires_workspace_inspection("Follow the roadmap") is True
    assert requires_workspace_inspection("Resolve issue #12") is True
    assert requires_workspace_inspection("Give me a stock analysis") is False


def test_explicit_workspace_paths_are_bounded_and_deduplicated():
    assert explicit_workspace_paths(
        "Compare ./README.md with cli/README.md and README.md"
    ) == ["README.md", "cli/README.md"]


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_hybrid_repo_research_prefetches_workspace_and_bounded_search(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.user_message = (
        "Review this repo and compare it with Cluade CLI for missing features"
    )
    state.prompt_mode = "code"
    state.requires_external_evidence = True
    state.execution_brief = "Translated objective:\n" + ("long objective " * 100)
    state.route_tasks = [
        {
            "objective": "Compare repository features with Cluade CLI " * 20,
            "workflow": "research",
        }
    ]
    mock_registry.list_tools.return_value = [
        "list_files",
        "project_summary",
        "read_file",
        "web_search",
        "web_fetch",
    ]
    mock_registry.execute.side_effect = [
        [{"name": "README.md", "type": "file"}],
        {
            "query": "Claude CLI",
            "results": [
                {
                    "title": "Claude Code CLI",
                    "url": "https://docs.example.test/claude",
                }
            ],
        },
        {"url": "https://docs.example.test/claude", "text": "Official CLI options"},
    ]
    mock_chat_with_tools.return_value = make_message(content="Compared.")

    assert execute_plan(state) == "Compared."

    calls = mock_registry.execute.call_args_list
    assert [call.args[0] for call in calls[:3]] == [
        "list_files",
        "web_search",
        "web_fetch",
    ]
    query = calls[1].args[1]["query"]
    assert "Claude CLI" in query
    assert "official documentation" in query
    assert len(query) <= 500
    tool_names = [
        tool["function"]["name"]
        for tool in mock_chat_with_tools.call_args.kwargs["tools"]
    ]
    assert "read_file" in tool_names
    assert "web_search" in tool_names
    assert "inspect_test_environment" not in tool_names
    system_prompt = mock_chat_with_tools.call_args.args[0][0]["content"]
    assert "autonomous software engineering agent" in system_prompt
    task_context = mock_chat_with_tools.call_args.args[0][1]["content"]
    assert "Verified workspace discovery" in task_context
    assert "Official CLI options" in task_context


@patch("app.agent.executor.chat", return_value="Synthesized from cached evidence.")
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_repeated_read_only_tool_calls_use_cache_and_force_synthesis(
    mock_chat_with_tools,
    mock_registry,
    mock_chat,
):
    state = DummyState()
    repeated_call = make_message(
        tool_calls=[make_tool_call("read", "read_file", {"file_path": "README.md"})]
    )
    mock_chat_with_tools.side_effect = [repeated_call, repeated_call, repeated_call]
    mock_registry.list_tools.return_value = ["read_file"]
    mock_registry.execute.return_value = "repository evidence"
    events = []

    assert (
        execute_plan(
            state, on_event=lambda kind, payload: events.append((kind, payload))
        )
        == "Synthesized from cached evidence."
    )

    mock_registry.execute.assert_called_once_with(
        "read_file", {"file_path": "README.md"}
    )
    assert [kind for kind, _ in events].count("duplicate_tool_call") == 2
    assert any(kind == "repeated_tool_loop" for kind, _ in events)
    mock_chat.assert_called_once()


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_edit_task_breaks_repeated_read_loop_and_performs_change(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()
    state.allow_write = True
    state.user_message = "Improve test coverage"
    repeated_read = make_message(
        tool_calls=[make_tool_call("read", "list_files", {"directory": "tests"})]
    )
    mock_chat_with_tools.side_effect = [
        repeated_read,
        repeated_read,
        repeated_read,
        make_message(
            tool_calls=[
                make_tool_call(
                    "edit",
                    "write_file",
                    {
                        "file_path": "tests/test_added.py",
                        "content": "def test_added(): pass\n",
                    },
                )
            ]
        ),
        make_message(
            tool_calls=[make_tool_call("verify", "run_tests", {"directory": "."})]
        ),
        make_message(content="Added and verified a focused coverage test."),
    ]
    mock_registry.list_tools.return_value = ["list_files", "write_file", "run_tests"]

    def execute(tool, _args):
        if tool == "list_files":
            return ["tests/test_existing.py"]
        if tool == "write_file":
            return {"status": "written", "path": "tests/test_added.py"}
        return {"exit_code": 0, "output": "1 passed"}

    mock_registry.execute.side_effect = execute

    assert execute_plan(state) == "Added and verified a focused coverage test."
    assert state.successful_mutation is True
    assert state.successful_verification is True
    assert any(
        "call an edit or write tool now" in message.get("content", "")
        for call in mock_chat_with_tools.call_args_list
        for message in call.args[0]
    )
    choices = [
        call.kwargs["tool_choice"] for call in mock_chat_with_tools.call_args_list
    ]
    assert choices[:3] == ["auto", "auto", "auto"]
    assert choices[3:5] == ["required", "required"]
    assert choices[-1] == "auto"


@patch("app.agent.executor.chat", return_value="Complete comparison report.")
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_hybrid_analysis_synthesizes_after_sufficient_evidence(
    mock_chat_with_tools,
    mock_registry,
    mock_chat,
):
    state = DummyState()
    state.user_message = "Review this repo and compare with Claude CLI"
    state.prompt_mode = "code"
    state.requires_external_evidence = True
    state.route_tasks = [
        {"objective": "Compare with Claude CLI", "workflow": "research"}
    ]
    mock_registry.list_tools.return_value = [
        "list_files",
        "inspect_files",
        "tree",
        "read_file",
        "web_search",
        "web_fetch",
    ]
    mock_registry.execute.side_effect = [
        [{"name": "README.md"}],
        {"items": [{"path": "cli/ROADMAP.md", "content": "current and future work"}]},
        {
            "results": [
                {
                    "url": "https://docs.example.test/claude",
                    "title": "Claude CLI",
                }
            ]
        },
        {"text": "official external evidence"},
    ]
    events = []

    assert (
        execute_plan(
            state, on_event=lambda kind, payload: events.append((kind, payload))
        )
        == "Complete comparison report."
    )

    mock_chat_with_tools.assert_not_called()
    synthesis = next(
        payload for kind, payload in events if kind == "evidence_synthesis"
    )
    assert synthesis["prefetched"] is True
    assert [call.args[0] for call in mock_registry.execute.call_args_list] == [
        "list_files",
        "inspect_files",
        "web_search",
        "web_fetch",
    ]
    mock_chat.assert_called_once()


@patch("app.agent.executor.chat_with_tools")
@patch("app.agent.executor.registry")
def test_finance_policy_prefetches_even_without_current_keyword(
    mock_registry, mock_chat_with_tools
):
    state = DummyState()
    state.user_message = "Analyze my uploaded portfolio for the long term"
    state.prompt_mode = "finance"
    state.requires_external_evidence = True
    state.execution_brief = "Analyze the DSE holdings Fortune Shoes and Orion Pharma"
    state.routing_entities = ["Fortune Shoes", "Orion Pharma"]
    mock_registry.list_tools.return_value = ["web_search"]
    mock_registry.execute.return_value = {"results": []}
    mock_chat_with_tools.return_value = type(
        "Message", (), {"content": "Evidence is unavailable.", "tool_calls": None}
    )()

    execute_plan(state, force_research=True)

    queries = [call.args[1]["query"] for call in mock_registry.execute.call_args_list]
    assert any("Fortune Shoes" in query for query in queries)
    assert any("Orion Pharma" in query for query in queries)


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_retries_research_refusal(mock_chat_with_tools, mock_registry):
    state = DummyState()
    state.user_message = "DSE stock closing price today"
    mock_registry.list_tools.return_value = ["web_search"]
    mock_registry.execute.return_value = {
        "results": [{"url": "https://example.test", "content": "Previous close 470.60"}]
    }
    mock_chat_with_tools.side_effect = [
        make_message(
            content="I cannot directly access real-time stock market data.",
            tool_calls=None,
        ),
        make_message(
            content="The retrieved result reports a previous close of 470.60.",
            tool_calls=None,
        ),
    ]

    assert (
        execute_plan(state)
        == "The retrieved result reports a previous close of 470.60."
    )
    assert mock_chat_with_tools.call_count == 2


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_reprompts_workspace_refusal_to_inspect_then_tool(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.user_message = "Review this project and from TODO list implement Tier 3"
    state.allow_write = True

    def execute(tool, _args):
        if tool == "list_files":
            return {"files": ["README.md", "TODO.md"]}
        if tool == "write_file":
            return {"status": "written", "path": "server/app/worker.py"}
        if tool == "run_tests":
            return {"exit_code": 0, "output": "1 passed"}
        return {"error": "unknown tool"}

    mock_registry.list_tools.return_value = ["list_files", "write_file", "run_tests"]
    mock_registry.execute.side_effect = execute
    mock_chat_with_tools.side_effect = [
        make_message(
            content=(
                "I am unable to proceed without the required files. "
                "Please provide the relevant source code and TODO list."
            ),
            tool_calls=None,
        ),
        make_message(
            tool_calls=[make_tool_call("call_1", "list_files", {"directory": "."})]
        ),
        make_message(
            tool_calls=[
                make_tool_call(
                    "call_2",
                    "write_file",
                    {
                        "file_path": "server/app/worker.py",
                        "content": "def spawn(): pass\n",
                    },
                )
            ]
        ),
        make_message(
            tool_calls=[make_tool_call("call_3", "run_tests", {"kind": "pytest"})]
        ),
        make_message(content="Inspected the workspace and implemented the change."),
    ]

    assert execute_plan(state) == "Inspected the workspace and implemented the change."

    assert mock_chat_with_tools.call_count == 5
    transcript = mock_chat_with_tools.call_args_list[1].args[0]
    assert any(
        "Inspect the repository yourself now" in str(message.get("content", ""))
        for message in transcript
    )


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_edit_request_rejects_recommendations_and_returns_to_tools(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.user_message = "Improve tests and verify them"
    state.allow_write = True
    mock_registry.list_tools.return_value = ["write_file", "run_tests"]
    mock_registry.execute.side_effect = [
        {"status": "written", "path": "tests/test_new.py"},
        {"kind": "pytest", "exit_code": 0, "output": "1 passed"},
    ]
    mock_chat_with_tools.side_effect = [
        make_message(content="I recommend adding tests."),
        make_message(
            tool_calls=[
                make_tool_call(
                    "write",
                    "write_file",
                    {"file_path": "tests/test_new.py", "content": "test"},
                )
            ]
        ),
        make_message(
            tool_calls=[make_tool_call("test", "run_tests", {"kind": "pytest"})]
        ),
        make_message(content="Added tests; 1 passed."),
    ]

    assert execute_plan(state) == "Added tests; 1 passed."
    assert mock_registry.execute.call_count == 2
    transcript = mock_chat_with_tools.call_args_list[1].args[0]
    assert any(
        "Do not revise the prose" in str(message.get("content", ""))
        for message in transcript
    )


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_retries_price_refusal_with_retrieved_evidence(
    mock_chat_with_tools, mock_registry
):
    state = DummyState()
    state.user_message = "Search Renata last closing price on DSE"
    mock_registry.list_tools.return_value = ["web_search"]
    mock_registry.execute.return_value = {
        "results": [{"url": "https://example.test", "content": "Previous close 470.60"}]
    }
    mock_chat_with_tools.side_effect = [
        make_message(
            content="I cannot provide the last closing day price based on current information."
        ),
        make_message(
            content="The source reports a previous close of 470.60.", tool_calls=None
        ),
    ]

    assert execute_plan(state) == "The source reports a previous close of 470.60."
    assert mock_chat_with_tools.call_count == 2


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_unavailable_tool(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    # write_file is not in the available tool list for this request.
    mock_registry.list_tools.return_value = []

    mock_chat_with_tools.side_effect = [
        make_message(
            content=None,
            tool_calls=[
                make_tool_call(
                    "call_1", "write_file", {"file_path": "x.py", "content": "y"}
                )
            ],
        ),
        make_message(content="Done", tool_calls=None),
    ]

    result = execute_plan(state)

    assert result == "Done"
    mock_registry.execute.assert_not_called()
    assert "unavailable" in state.observations[0]["result"]["error"]


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_empty_content_retries(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    # A response with neither tool_calls nor usable content must be treated
    # as invalid and retried, rather than accepted as an empty final answer.
    mock_chat_with_tools.side_effect = [
        make_message(content="", tool_calls=None),
        make_message(content="Recovered", tool_calls=None),
    ]

    result = execute_plan(state)

    assert result == "Recovered"
    assert mock_chat_with_tools.call_count == 2


@patch(
    "app.agent.executor._synthesize_partial_answer",
    return_value="Partial evidence-based answer",
)
@patch("app.agent.executor.MAX_EMPTY_MODEL_TURNS", 3)
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_synthesizes_after_repeated_empty_turns(
    mock_chat_with_tools,
    mock_registry,
    mock_synthesize,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    # The model never calls a tool and never produces usable content. The
    # executor must synthesize a partial answer instead of burning all steps.
    mock_chat_with_tools.return_value = make_message(content="", tool_calls=None)

    result = execute_plan(state)

    assert result == "Partial evidence-based answer"
    assert mock_chat_with_tools.call_count == 3
    mock_synthesize.assert_called_once_with(state)


@patch(
    "app.agent.executor._synthesize_partial_answer",
    return_value="Useful partial answer",
)
@patch("app.agent.executor.MAX_EMPTY_SEARCH_RESULTS", 3)
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_stops_after_repeated_empty_searches(
    mock_chat_with_tools,
    mock_registry,
    mock_synthesize,
):
    state = DummyState()
    mock_registry.list_tools.return_value = ["search_text"]
    mock_registry.execute.return_value = []
    mock_chat_with_tools.return_value = make_message(
        tool_calls=[make_tool_call("call", "search_text", {"keyword": "irrelevant"})]
    )

    events = []
    answer = execute_plan(
        state, on_event=lambda kind, payload: events.append((kind, payload))
    )
    assert answer.startswith("Useful partial answer")
    assert "Incomplete requirements:" in answer
    assert "unresolved failed tool categories: inspection" in answer

    assert mock_chat_with_tools.call_count == 3
    mock_synthesize.assert_called_once_with(state)
    assert ("unproductive_search_loop", {"empty_searches": 3}) in events


@patch(
    "app.agent.executor._synthesize_partial_answer",
    return_value="Useful partial answer",
)
@patch("app.agent.executor.MAX_UNPRODUCTIVE_TOOL_CALLS", 3)
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_stops_after_repeated_failed_tool_calls(
    mock_chat_with_tools,
    mock_registry,
    mock_synthesize,
):
    state = DummyState()
    mock_registry.list_tools.return_value = ["read_file"]
    mock_registry.execute.return_value = {"error": "File not found"}
    mock_chat_with_tools.return_value = make_message(
        tool_calls=[make_tool_call("call", "read_file", {"file_path": "missing.py"})]
    )

    events = []
    answer = execute_plan(
        state, on_event=lambda kind, payload: events.append((kind, payload))
    )
    assert answer.startswith("Useful partial answer")
    assert "Incomplete requirements:" in answer
    assert "unresolved failed tool categories: inspection" in answer

    assert mock_chat_with_tools.call_count == 3
    mock_synthesize.assert_called_once_with(state)
    assert ("unproductive_tool_loop", {"repeated_calls": 3}) in events


@patch("app.agent.executor.replan", return_value=["read error log", "fix root cause"])
@patch("app.agent.executor.REPLAN_FAIL_STREAK", 3)
@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_replans_after_consecutive_failures(
    mock_chat_with_tools,
    mock_registry,
    mock_replan,
):
    state = DummyState()
    state.reflection_run = True
    mock_registry.list_tools.return_value = ["read_file"]
    mock_registry.execute.return_value = {"error": "File not found"}
    seen_messages = []
    events = []
    turns = 0

    def respond(messages, **kwargs):
        nonlocal turns
        turns += 1
        seen_messages.append([dict(message) for message in messages])
        if turns < 4:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "call",
                        "read_file",
                        {"file_path": f"missing_{turns}.py"},
                    )
                ]
            )
        return make_message(content="Blocked by missing source files.")

    mock_chat_with_tools.side_effect = respond

    answer = execute_plan(
        state, on_event=lambda kind, payload: events.append((kind, payload))
    )

    assert answer.startswith("Blocked by missing source files.")
    assert mock_chat_with_tools.call_count == 5
    mock_replan.assert_called_once()
    assert mock_replan.call_args.args[0] is state
    assert "read_file" in mock_replan.call_args.args[1]

    replanned = [payload for kind, payload in events if kind == "replanned"]
    assert len(replanned) == 1
    assert replanned[0]["reason"] == "3 consecutive tool-call steps failed"
    assert replanned[0]["new_plan"] == ["read error log", "fix root cause"]
    assert state.plan == ["read error log", "fix root cause"]
    escalated = [payload for kind, payload in events if kind == "model_escalated"]
    assert escalated == [
        {
            "from": "test-model",
            "to": AGENT_REASONING_MODEL,
            "reason": "3 consecutive tool-call steps failed",
            "escalation": 1,
        }
    ]
    assert state.model == AGENT_REASONING_MODEL
    assert state.model_escalations == 1

    last_turn = seen_messages[-1]
    assert any(
        message["role"] == "user" and "Follow this revised plan" in message["content"]
        for message in last_turn
    )
    assert any(
        message["role"] == "user"
        and "Reasoning-model escalation handoff" in message["content"]
        for message in last_turn
    )


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_instructs_model_to_recover_after_failed_command(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()
    state.allow_write = True
    mock_registry.list_tools.return_value = ["run_command", "run_tests"]
    mock_registry.execute.side_effect = [
        {
            "command": "pytest --cov=app",
            "exit_code": 4,
            "output": "unrecognized arguments: --cov=app",
        },
        {"kind": "pytest", "exit_code": 0, "output": "10 passed"},
    ]
    seen_messages = []
    events = []

    def respond(messages, **kwargs):
        seen_messages.append([dict(message) for message in messages])
        if len(seen_messages) == 1:
            return make_message(
                tool_calls=[
                    make_tool_call(
                        "coverage",
                        "run_command",
                        {"command": "pytest --cov=app"},
                    )
                ]
            )
        if len(seen_messages) == 2:
            return make_message(
                content=(
                    "Let's first check the pytest version and then run the tests "
                    "with the correct coverage options."
                )
            )
        if len(seen_messages) == 3:
            return make_message(
                tool_calls=[make_tool_call("fallback", "run_tests", {"kind": "pytest"})]
            )
        return make_message(content="Tests pass using the available test runner.")

    mock_chat_with_tools.side_effect = respond

    assert (
        execute_plan(
            state,
            on_event=lambda kind, payload: events.append((kind, payload)),
        )
        == "Tests pass using the available test runner."
    )

    recovery_messages = [
        message["content"]
        for message in seen_messages[1]
        if message["role"] == "user" and "A tool call failed" in message["content"]
    ]
    assert len(recovery_messages) == 1
    assert "Do not stop at a future-tense proposal" in recovery_messages[0]
    retry_messages = [
        message["content"]
        for message in seen_messages[2]
        if message["role"] == "user" and "progress announcement" in message["content"]
    ]
    assert len(retry_messages) == 1
    assert "explicit path" in retry_messages[0]
    assert any(kind == "premature_handoff_retry" for kind, _ in events)
    assert mock_registry.execute.call_count == 2


@patch("app.agent.executor.chat", return_value="Evidence-based answer")
def test_partial_synthesis_preserves_early_useful_evidence(mock_chat):
    state = DummyState()
    state.observations = [
        {"tool": "list_files", "result": [{"path": "README.md"}]},
        {"tool": "read_file", "result": "# AI Stack\nArchitecture details"},
        *[{"tool": "search_text", "result": []} for _ in range(10)],
    ]

    assert _synthesize_partial_answer(state) == "Evidence-based answer"
    synthesis_prompt = mock_chat.call_args.args[0][1]["content"]
    assert "README.md" in synthesis_prompt
    assert "Architecture details" in synthesis_prompt


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_emits_events(
    mock_chat_with_tools,
    mock_registry,
):
    """The streaming hook used by durable /runs execution should observe at
    least a step_started and a final_answer event."""
    state = DummyState()

    mock_registry.list_tools.return_value = []
    mock_chat_with_tools.return_value = make_message(content="Done", tool_calls=None)

    events = []
    execute_plan(
        state,
        on_event=lambda event_type, payload: events.append((event_type, payload)),
    )

    event_types = [event_type for event_type, _ in events]
    assert "step_started" in event_types
    assert "final_answer" in event_types


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools_stream")
def test_execute_plan_buffers_text_until_completion_audit(mock_stream, mock_registry):
    state = DummyState()
    mock_registry.list_tools.return_value = []
    mock_stream.return_value = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(content="Hel", tool_calls=None))
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(content="lo", tool_calls=None))
            ]
        ),
    ]
    tokens = []

    result = execute_plan(state, on_token=tokens.append)

    assert result == "Hello"
    assert tokens == ["Hello"]


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools_stream")
def test_streaming_completion_repairs_rejected_draft_before_emitting(
    mock_stream, mock_registry
):
    state = DummyState()
    state.route_deliverables = ["Coverage report"]
    mock_registry.list_tools.return_value = []
    mock_stream.side_effect = [
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="Done", tool_calls=None)
                    )
                ]
            )
        ],
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="Coverage report complete", tool_calls=None
                        )
                    )
                ]
            )
        ],
    ]
    tokens = []

    result = execute_plan(state, on_token=tokens.append)

    assert result == "Coverage report complete"
    assert tokens == ["Coverage report complete"]
    assert mock_stream.call_count == 2


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools_stream")
def test_execute_plan_reassembles_streamed_tool_arguments(mock_stream, mock_registry):
    state = DummyState()
    mock_registry.list_tools.return_value = ["list_files"]
    mock_registry.execute.return_value = {"files": ["README.md"]}
    mock_stream.side_effect = [
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call-1",
                                    function=SimpleNamespace(
                                        name="list_files", arguments='{"directory":'
                                    ),
                                )
                            ],
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id=None,
                                    function=SimpleNamespace(
                                        name=None, arguments='"."}'
                                    ),
                                )
                            ],
                        )
                    )
                ]
            ),
        ],
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="Done", tool_calls=None)
                    )
                ]
            )
        ],
    ]

    result = execute_plan(state, on_token=lambda _: None)

    assert result == "Done"
    mock_registry.execute.assert_called_once_with("list_files", {"directory": "."})
