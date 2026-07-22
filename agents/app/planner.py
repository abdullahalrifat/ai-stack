from .llm import chat
from .prompts import PLANNER_PROMPT
from .parser import extract_json



def create_plan(state):


    response = chat(

        [

            {
                "role":"system",
                "content": PLANNER_PROMPT
            },


            {
                "role":"user",
                "content": state.user_message
            }

        ]

    )


    data = extract_json(
        response
    )


    return data.get(
        "plan",
        []
    )