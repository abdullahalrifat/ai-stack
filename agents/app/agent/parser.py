# app/parser.py

"""
Parser utilities.

The executor now uses native OpenAI-style function calling (see llm.py /
tool_schemas.py / executor.py), so it no longer needs to regex-extract JSON
tool calls out of free text. These helpers are still used by:

- planner.py, which asks the model for a plain JSON plan (no tool schema
  needed for that -- it's just a list of strings).
- executor.py, to normalize a tool call's `arguments` payload, which the
  OpenAI API delivers as a JSON-encoded string.
"""

import json
import re
from typing import Any

from .state import AgentAction, AgentPlan


class ParserError(Exception):
    pass


def extract_json(text: str) -> dict[str, Any]:
    """
    Extract a JSON object from LLM response text.
    Handles cases where the model wraps JSON in markdown fences.
    """

    if not text:
        raise ParserError("Empty LLM response")

    text = text.strip()

    # Remove markdown fences
    text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "").strip()

    # Find JSON object
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)

    if not match:
        raise ParserError(f"No JSON found in response: {text[:200]}")

    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        raise ParserError(f"Invalid JSON: {e}")


def parse_plan(response: str) -> AgentPlan:
    """
    Convert planner LLM output into AgentPlan (legacy helper; the planner
    itself works directly off `extract_json` today, kept for callers that
    want a typed plan object).
    """

    data = extract_json(response)

    actions = []

    for item in data.get("actions", []):
        if "tool" not in item:
            continue

        actions.append(
            AgentAction(
                tool=item["tool"],
                args=item.get("args", {}),
            )
        )

    return AgentPlan(steps=actions)


def parse_tool_arguments(args: Any) -> dict[str, Any]:
    """
    Normalize a tool call's arguments into a dict.

    Native function-calling APIs deliver `arguments` as a JSON-encoded
    string; this also tolerates already-parsed dicts and None.
    """

    if args is None:
        return {}

    if isinstance(args, dict):
        return args

    if isinstance(args, str):
        if not args.strip():
            return {}
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return {"input": args}
        return parsed if isinstance(parsed, dict) else {"input": parsed}

    raise ParserError(f"Unsupported argument type: {type(args)}")


def extract_final_answer(response: str) -> str | None:
    """
    Extract a final answer from a legacy JSON-formatted response, if any
    caller still produces that shape.
    """

    data = extract_json(response)
    return data.get("final_answer")


def validate_action(action: dict[str, Any]) -> bool:
    """
    Basic validation before execution.
    """

    required = ["tool"]
    return all(field in action for field in required)


def validate_agent_response(data):

    if "tool" in data:
        return isinstance(data.get("tool"), str) and isinstance(
            data.get("args", {}), dict
        )

    if "final_answer" in data:
        answer = data.get("final_answer")
        return isinstance(answer, str) and len(answer.strip()) > 20

    return False