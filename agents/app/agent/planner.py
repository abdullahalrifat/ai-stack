import logging

from ..core.config import DEFAULT_MODEL, PLANNER_MAX_COMPLETION_TOKENS
from ..llm.client import chat
from .parser import ParserError, extract_json
from .prompts import PLANNER_PROMPT

logger = logging.getLogger(__name__)


def create_plan(state):
    response = chat(
        [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        model=getattr(state, "model", None) or DEFAULT_MODEL,
        max_tokens=PLANNER_MAX_COMPLETION_TOKENS,
    )

    try:
        data = extract_json(response)
    except ParserError:
        # Planning is only guidance for the tool loop. A local model can stop
        # midway through JSON; never discard the entire user run for that.
        logger.warning("Ignoring incomplete planner response")
        return []

    plan = data.get("plan", [])
    return plan[:5] if isinstance(plan, list) else []
