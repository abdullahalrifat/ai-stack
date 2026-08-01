import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app.agent.completion import record_tool_progress
from app.agent.executor import (
    _answer_audit,
    _prefetch_workspace,
    _synthesize_partial_answer,
    execute_plan,
    explicit_workspace_paths,
    financial_document_excerpt,
    financial_document_urls,
    financial_price_query,
    financial_research_queries,
    normalize_tool_args,
    requires_workspace_inspection,
    report_pdf_link,
    tool_result_failed,
)
from app.agent.prompts import executor_prompt


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


def test_failed_guess_does_not_invalidate_successful_inspection():
    state = DummyState()

    record_tool_progress(state, "list_files", {"directory": "."}, ["agents/tests"])
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
    assert checkpoints == [
        {
            "steps": 1,
            "plan": [],
            "observations": [
                {"tool": "list_files", "result": {"files": ["README.md"]}}
            ],
            "route_tasks": [],
            "task_progress": {},
            "successful_mutation": False,
            "successful_verification": False,
            "pending_failure_categories": [],
        }
    ]


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
                    {"file_path": "tests/test_added.py", "content": "def test_added(): pass\n"},
                )
            ]
        ),
        make_message(
            tool_calls=[
                make_tool_call("verify", "run_tests", {"directory": "."})
            ]
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
