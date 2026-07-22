import json

from .llm import chat

from .parser import (
    extract_json,
    parse_tool_arguments,
    validate_agent_response,
)

from .tool_registry import registry

from .prompts import EXECUTOR_PROMPT


MAX_STEPS = 15

MIN_TOOL_CALLS_BEFORE_FINAL = 2



def execute_plan(state):

    available_tools = registry.list_tools()


    def observation_json():

        return json.dumps(
            state.observations[-10:],
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

{observation_json()}


Rules:

- You are a software engineering agent.
- All repositories exist inside workspace.
- Never guess file contents.
- Always inspect files before answering.
- For repository analysis:
    1. list files
    2. read important configuration files
    3. inspect source files
- Do not produce final_answer after only list_files.
- Use tools until investigation is complete.
- Output ONLY valid JSON.


Tool format:

{{
    "tool":"tool_name",
    "args":{{}}
}}


Final format:

{{
    "final_answer":"actual answer"
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
                "INVALID JSON:",
                e
            )


            messages.append(
                {
                    "role":"user",
                    "content":
                    """
Your output was invalid.

Return ONLY JSON.

Allowed:

{
 "tool":"tool_name",
 "args":{}
}

or

{
 "final_answer":"answer"
}
"""
                }
            )

            continue



        #
        # Handle wrong model format
        #

        if data.get("decision") == "final_answer":

            data = {
                "final_answer":
                    data.get(
                        "reason",
                        "Investigation completed."
                    )
            }



        #
        # Final answer validation
        #

        if "final_answer" in data:


            if len(state.observations) < MIN_TOOL_CALLS_BEFORE_FINAL:


                messages.append(
                    {
                        "role":"user",
                        "content":
                        """
You finished too early.

More investigation is required.

Inspect repository files before answering.

Return ONLY JSON.
"""
                    }
                )

                continue



            state.finished = True

            return data["final_answer"]




        #
        # Tool call
        #

        tool = data.get("tool")


        if not tool:


            messages.append(
                {
                    "role":"user",
                    "content":
                    """
No valid action.

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
                "error": str(e)
            }



        state.add_tool(
            tool,
            result
        )


        tool_result = json.dumps(
            result,
            indent=2,
            default=str
        )



        messages.append(
            {
                "role":"assistant",
                "content":response
            }
        )


        messages.append(
            {
                "role":"user",
                "content":f"""
Tool executed:

{tool}


Result:

{tool_result}


Current observations:

{observation_json()}


Continue.

If more information is needed:

Return:

{{
 "tool":"tool_name",
 "args":{{}}
}}


If finished:

Return:

{{
 "final_answer":"actual detailed answer"
}}


ONLY JSON.
"""
            }
        )



    return (
        "Maximum execution steps reached before completion."
    )