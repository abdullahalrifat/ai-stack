from .config import DEFAULT_MODEL
from .llm import chat
from .parser import extract_json
from .prompts import PLANNER_PROMPT


def create_plan(state):
    response = chat(
        [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        model=getattr(state, "model", None) or DEFAULT_MODEL,
    )

    data = extract_json(response)

    return data.get("plan", [])
