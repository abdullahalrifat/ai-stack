from .llm import chat
from .parser import extract_json
from .prompts import PLANNER_PROMPT


def create_plan(state):
    response = chat(
        [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": state.user_message},
        ]
    )

    data = extract_json(response)

    return data.get("plan", [])