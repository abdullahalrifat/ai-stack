import json

from .llm import chat

from .parser import (
    extract_json,
    parse_tool_arguments,
    validate_agent_response
)

from .tool_registry import registry

from .prompts import EXECUTOR_PROMPT


MAX_STEPS = 15



def execute_plan(state):


    available_tools = registry.list_tools()
    previous_observations = json.dumps(
            state.observations,
            indent=2,
            default=str
        )

    messages = [

        {
            "role": "system",
            "content": EXECUTOR_PROMPT
        },


        {
            "role": "user",
            "content": f"""
Workspace:

{state.workspace}


Task:

{state.user_message}


Plan:

{state.plan}


Available tools:

{available_tools}


Previous observations:

{previous_observations}



You are an autonomous engineering agent.

Rules:

1. Repositories exist inside workspace.
2. Never invent paths.
3. Inspect files before making conclusions.
4. Use tools whenever you need information.
5. Do not answer until investigation is complete.
6. Your response MUST be valid JSON.


Tool call format:

{{
    "tool": "tool_name",
    "args": {{
        "argument": "value"
    }}
}}


Final response format:

{{
    "final_answer": "your complete answer"
}}

"""
        }

    ]



    for step in range(MAX_STEPS):


        state.steps += 1


        response = chat(messages)



        print(
            "EXECUTOR RESPONSE:",
            response
        )



        try:

            data = extract_json(response)


        except Exception as e:

            print(
                "JSON PARSE FAILED:",
                e
            )


            # Ask model to correct itself

            messages.append(

                {
                    "role":
                        "assistant",

                    "content":
                        response
                }

            )


            messages.append(

                {
                    "role":
                        "user",

                    "content":
                    """
Your previous response was not valid JSON.

Return ONLY JSON.

Use:

{
 "tool": "...",
 "args": {}
}

or:

{
 "final_answer": "..."
}
"""
                }

            )


            continue




        #
        # Handle incorrect decision format
        #

        if data.get("decision") == "final_answer":


            messages.append(

                {
                    "role":
                        "user",

                    "content":
                    """
You selected final_answer.

Now provide the actual answer.

Return ONLY:

{
 "final_answer": "answer text"
}
"""
                }

            )


            continue



        #
        # Completed
        #

        if validate_agent_response(data) and "final_answer" in data:

            state.finished = True


            return data["final_answer"]




        #
        # Tool request
        #

        tool = data.get(
            "tool"
        )


        if not tool:


            messages.append(

                {
                    "role":
                        "user",

                    "content":
                    """
No valid tool or final_answer detected.

Continue investigation.

Return ONLY JSON.
"""
                }

            )


            continue



        args = parse_tool_arguments(

            data.get(
                "args",
                {}
            )

        )



        print(
            "EXECUTING TOOL:",
            tool,
            args
        )



        try:


            result = registry.execute(

                tool,

                args

            )


        except Exception as e:


            result = {

                "error":
                    str(e)

            }



        state.add_tool(

            tool,

            result

        )



        #
        # Continue reasoning
        #

        messages.append(

            {
                "role":
                    "assistant",

                "content":
                    response
            }

        )


        messages.append(

            {
                "role":
                    "user",

                "content":
                f"""
Tool result:

Tool:
{tool}


Result:

{result}


Continue.


Remember:

If more information is required:
use another tool.

If investigation is complete:
return:

{
 "final_answer": "WRITE THE ACTUAL FINAL RESPONSE HERE"
}


The value of final_answer must contain the real answer to the user.
Do not use placeholder text.

Return ONLY JSON.
"""
            }

        )



    return {
        "final_answer":
            "Maximum execution steps reached before completion."
    }