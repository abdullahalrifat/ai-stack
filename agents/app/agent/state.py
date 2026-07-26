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

    model: str = DEFAULT_MODEL

    prompt_mode: str = "code"

    execution_brief: str = ""

    routing_entities: list[str] = field(default_factory=list)

    route_deliverables: list[str] = field(default_factory=list)

    route_completion_criteria: list[str] = field(default_factory=list)

    requires_external_evidence: bool = False

    max_completion_tokens: int | None = None

    timeout_seconds: int | None = None

    allow_write: bool = False

    plan: list = field(default_factory=list)

    messages: list = field(default_factory=list)

    steps: int = 0

    max_steps: int = 15

    finished: bool = False

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


@dataclass
class AgentAction:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class AgentPlan:
    steps: list = field(default_factory=list)
