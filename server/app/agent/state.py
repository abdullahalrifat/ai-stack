from dataclasses import dataclass, field
from typing import Any

from ..core.config import DEFAULT_MODEL, DEFAULT_WORKSPACE, MAX_TOOL_OUTPUT_CHARS


@dataclass
class AgentState:
    conversation_id: str

    user_message: str

    workspace: str = str(DEFAULT_WORKSPACE)

    history: list = field(default_factory=list)

    memories: list = field(default_factory=list)

    document_evidence: dict = field(default_factory=dict)

    memory_scope: str | None = None

    observations: list = field(default_factory=list)

    # Structured task evidence survives prompt compaction and lets the
    # executor reason about what is confirmed, missing, and still open without
    # replaying every raw file read to the model.
    evidence_ledger: dict[str, Any] = field(
        default_factory=lambda: {
            "requirements": [],
            "relevant_files": [],
            "owning_symbols": [],
            "test_targets": [],
            "dependencies": {},
            "confirmed_existing": [],
            "confirmed_missing": [],
            "open_questions": [],
        }
    )

    roadmap_requirements: list[str] = field(default_factory=list)

    active_roadmap_item: str = ""

    active_requirement: str = ""

    graph_phase: str = "pending"

    graph_history: list[dict[str, str]] = field(default_factory=list)

    model_escalations: int = 0

    original_model: str = ""

    diff_review: dict[str, Any] = field(default_factory=dict)

    successful_mutation: bool = False

    # Workspace-relative paths successfully written/edited this run. Used to
    # detect checklist-gaming: an "implement X" request whose only mutation is
    # a TODO/README markdown file is not an implementation.
    successful_mutation_paths: set[str] = field(default_factory=set)

    successful_verification: bool = False

    # Immutable execution-derived proof used by the v0.4 completion gate.
    # These records are populated only by observed successful tool execution;
    # the model never writes them directly.
    mutation_events: list[dict[str, Any]] = field(default_factory=list)

    verification_events: list[dict[str, Any]] = field(default_factory=list)

    # When an implementation request finalizes with only doc/marker-file
    # mutations, the run must not offer a pending diff for approval. The
    # executor sets this flag so the service layer discards the sandbox.
    diff_blocked: bool = False

    pending_failure_categories: set[str] = field(default_factory=set)

    successful_tool_categories: set[str] = field(default_factory=set)

    model: str = DEFAULT_MODEL

    prompt_mode: str = "code"

    execution_brief: str = ""

    routing_entities: list[str] = field(default_factory=list)

    route_deliverables: list[str] = field(default_factory=list)

    route_completion_criteria: list[str] = field(default_factory=list)

    route_tasks: list[dict[str, Any]] = field(default_factory=list)

    # When True the executor dispatches several bounded expert analyses in
    # parallel before the tool loop and merges their structured findings into
    # the evidence ledger. Set by the service layer for complex or
    # multi-workflow auto-routed requests, never inferred from message text.
    expert_dispatch: bool = False

    # Validated structured findings from the multi-expert dispatch, one item
    # per expert role (see dispatch.py). Each carries findings/open_questions/
    # recommended_focus and is bounded before it reaches model context.
    expert_findings: list[dict[str, Any]] = field(default_factory=list)

    task_progress: dict[str, str] = field(default_factory=dict)

    requires_external_evidence: bool = False

    max_completion_tokens: int | None = None

    timeout_seconds: int | None = None

    allow_write: bool = False

    # Optional injected PermissionPolicy. When None the executor derives the
    # policy from allow_write plus platform configuration.
    permissions: Any = None

    plan: list = field(default_factory=list)

    messages: list = field(default_factory=list)

    restored_transcript: list = field(default_factory=list)

    steps: int = 0

    max_steps: int = 15

    finished: bool = False

    partial: bool = False

    answer: str | None = None

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str):
        self.messages.append({"role": "assistant", "content": content})

    def add_tool(self, name: str, result: Any):
        # Tool results can be large directory listings/documents. Never retain
        # the unbounded object in agent state across a multi-step run.
        content = str(result)
        if len(content) > MAX_TOOL_OUTPUT_CHARS:
            content = content[:MAX_TOOL_OUTPUT_CHARS] + "\n...[tool result truncated]"
            result = {"truncated": True, "preview": content}
        self.observations.append({"tool": name, "result": result})
        self.messages.append({"role": "tool", "name": name, "content": content})

    def reset(self):
        self.steps = 0
        self.finished = False
        self.answer = None
        self.plan.clear()
        self.observations.clear()
        self.messages.clear()
        self.mutation_events.clear()
        self.verification_events.clear()


@dataclass
class AgentAction:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class AgentPlan:
    steps: list = field(default_factory=list)
