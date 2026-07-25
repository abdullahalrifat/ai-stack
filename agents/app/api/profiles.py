"""Open WebUI-visible agent profiles and their execution policy."""

from dataclasses import dataclass

from app.core.config import AGENT_MODEL_ID, DEFAULT_MODEL, RESEARCH_MODEL


@dataclass(frozen=True)
class AgentProfile:
    model: str
    force_research: bool = False


# `coding-agent` is retained for existing Open WebUI conversations.
PROFILES: dict[str, AgentProfile] = {
    AGENT_MODEL_ID: AgentProfile(DEFAULT_MODEL),
    "auto": AgentProfile(DEFAULT_MODEL),
    "code": AgentProfile(DEFAULT_MODEL),
    "research": AgentProfile(RESEARCH_MODEL, force_research=True),
    "finance": AgentProfile(RESEARCH_MODEL, force_research=True),
    "deep": AgentProfile("reasoning"),
    "vision": AgentProfile("vision"),
}


def resolve_profile(model_id: str | None) -> AgentProfile:
    if not model_id or model_id == AGENT_MODEL_ID:
        return PROFILES[AGENT_MODEL_ID]
    try:
        return PROFILES[model_id]
    except KeyError as exc:
        raise ValueError(f"Unknown agent profile '{model_id}'") from exc
