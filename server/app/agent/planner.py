import json
import logging

from ..core.config import (
    DEFAULT_MODEL,
    PLANNER_MAX_COMPLETION_TOKENS,
    REPLAN_MAX_COMPLETION_TOKENS,
)
from ..llm.client import chat
from .parser import ParserError, extract_json
from .prompts import PLANNER_PROMPT, REPLAN_PROMPT

logger = logging.getLogger(__name__)


def deterministic_plan(state) -> list[str]:
    """Low-latency plan for routine repository work without another LLM turn."""

    plan = [
        "Inspect the repository root and project documentation.",
        "Read the relevant configuration, manifests, source, and tests.",
        "Summarize evidence-backed findings and verification results.",
    ]
    if getattr(state, "allow_write", False):
        plan.insert(2, "Make the smallest scoped change and run relevant checks.")
    return plan


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


def replan(state, failure_context: str) -> list[str]:
    """Revise a stalled plan given the failures the executor actually observed.

    Returns the revised plan, or the current plan unchanged when the model
    cannot produce valid JSON so the loop never discards its guidance.
    """

    current_plan = list(getattr(state, "plan", []) or [])
    user = f"""Original task:
{state.user_message}

Current plan:
{json.dumps(current_plan, ensure_ascii=False) if current_plan else "(none)"}

Failures / unproductive steps so far:
{failure_context[:3_500]}
"""
    try:
        response = chat(
            [
                {"role": "system", "content": REPLAN_PROMPT},
                {"role": "user", "content": user},
            ],
            model=getattr(state, "model", None) or DEFAULT_MODEL,
            max_tokens=REPLAN_MAX_COMPLETION_TOKENS,
        )
        data = extract_json(response)
    except ParserError:
        logger.warning("Ignoring invalid re-plan response; keeping current plan")
        return current_plan
    except Exception:
        logger.exception("Re-planning failed; keeping current plan")
        return current_plan

    revised = data.get("plan", []) if isinstance(data, dict) else []
    if not isinstance(revised, list):
        return current_plan
    return revised[:5]
