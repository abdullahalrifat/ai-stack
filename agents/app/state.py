from dataclasses import dataclass, field
from typing import Any

from .config import DEFAULT_MODEL


@dataclass
class AgentState:
    conversation_id: str

    user_message: str

    workspace: str = "/workspace"

    history: list = field(default_factory=list)

    memories: list = field(default_factory=list)

    observations: list = field(default_factory=list)

    model: str = DEFAULT_MODEL

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
        self.observations.append({"tool": name, "result": result})
        self.messages.append({"role": "tool", "name": name, "content": str(result)})

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