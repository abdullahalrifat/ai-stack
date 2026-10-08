"""Concrete model IDs and their execution policy.

Model IDs are the only public selectors. Workflow names are internal routing
concepts and are never exposed as model aliases.
"""

from dataclasses import dataclass

from app.core.config import (
    AGENT_REASONING_MODEL,
    DEFAULT_MODEL,
    FAST_MODEL,
    FINANCE_LLM_TIMEOUT_SECONDS,
    FINANCE_MAX_COMPLETION_TOKENS,
    FINANCE_MODEL,
    RESEARCH_MODEL,
    RESEARCH_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class AgentProfile:
    model: str
    prompt_mode: str = "default"
    force_research: bool = False
    max_completion_tokens: int | None = None
    timeout_seconds: int | None = None


WORKFLOW_POLICIES: dict[str, AgentProfile] = {
    "quick": AgentProfile(FAST_MODEL, "fast"),
    "code": AgentProfile(AGENT_REASONING_MODEL, "code"),
    "research": AgentProfile(
        RESEARCH_MODEL,
        "research",
        force_research=True,
        timeout_seconds=RESEARCH_TIMEOUT_SECONDS,
    ),
    "finance": AgentProfile(
        FINANCE_MODEL,
        "finance",
        force_research=True,
        max_completion_tokens=FINANCE_MAX_COMPLETION_TOKENS,
        timeout_seconds=FINANCE_LLM_TIMEOUT_SECONDS,
    ),
    "deep": AgentProfile(AGENT_REASONING_MODEL, "deep"),
    "vision": AgentProfile(AGENT_REASONING_MODEL, "vision"),
}

# Public selectors are concrete model IDs only. Multiple workflows may share
# the same model, so workflow policy must not be encoded in a model-id map.
PROFILES: dict[str, AgentProfile] = {
    model_id: AgentProfile(model_id)
    for model_id in dict.fromkeys(
        (DEFAULT_MODEL, FAST_MODEL, RESEARCH_MODEL, FINANCE_MODEL, AGENT_REASONING_MODEL)
    )
}


def resolve_profile(model_id: str | None) -> AgentProfile:
    model_id = model_id or DEFAULT_MODEL
    try:
        return PROFILES[model_id]
    except KeyError as exc:
        raise ValueError(f"Unknown model '{model_id}'") from exc


def resolve_workflow(workflow: str) -> AgentProfile:
    try:
        return WORKFLOW_POLICIES[workflow]
    except KeyError as exc:
        raise ValueError(f"Unknown workflow '{workflow}'") from exc
