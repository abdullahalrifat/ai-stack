# agent/parser.py

"""
Parser utilities for converting LLM responses into structured agent plans/actions.

Expected LLM output format:

{
    "thought": "Need to inspect files",
    "actions": [
        {
            "tool": "filesystem.read_file",
            "args": {
                "path": "/app/test.py"
            }
        }
    ]
}
"""

import json
import re
from typing import Any, Dict, List, Optional

from .state import AgentAction, AgentPlan


class ParserError(Exception):
    pass


def extract_json(text: str) -> Dict[str, Any]:
    """
    Extract JSON object from LLM response.
    Handles cases where model wraps JSON in markdown.
    """

    if not text:
        raise ParserError("Empty LLM response")

    text = text.strip()

    # Remove markdown fences
    text = re.sub(
        r"```(?:json)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = text.replace("```", "").strip()

    # Find JSON object
    match = re.search(
        r"\{.*\}",
        text,
        flags=re.DOTALL
    )

    if not match:
        raise ParserError(
            f"No JSON found in response: {text[:200]}"
        )

    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        raise ParserError(
            f"Invalid JSON: {e}"
        )


def parse_plan(response: str) -> AgentPlan:
    """
    Convert planner LLM output into AgentPlan.
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

    return AgentPlan(
        thought=data.get("thought", ""),
        actions=actions,
    )


def parse_tool_arguments(
    args: Any
) -> Dict[str, Any]:
    """
    Normalize tool arguments.
    """

    if args is None:
        return {}

    if isinstance(args, dict):
        return args

    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {
                "input": args
            }

    raise ParserError(
        f"Unsupported argument type: {type(args)}"
    )


def extract_final_answer(response: str) -> Optional[str]:
    """
    Extract final response when agent finishes.
    """

    data = extract_json(response)

    return data.get(
        "final_answer"
    )


def validate_action(action: Dict[str, Any]) -> bool:
    """
    Basic validation before execution.
    """

    required = [
        "tool",
    ]

    return all(
        field in action
        for field in required
    )

def validate_agent_response(data):

    if "tool" in data:

        if not isinstance(data.get("args"), dict):
            return False

        return True


    if "final_answer" in data:

        answer = data["final_answer"]

        if answer in [
            "complete explanation",
            "answer text",
            "your answer",
            ""
        ]:
            return False

        return True


    return False