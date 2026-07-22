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


Rules:

1. Repositories exist inside workspace.
2. Never invent paths.
3. Inspect files before making conclusions.
4. Use tools whenever information is missing.
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
    "final_answer": "actual answer to the user"
}}


Never return placeholders.
The final_answer field must contain the real response.
"""
        }

    ]



    for step in range(MAX_STEPS):


        state.steps += 1


        response = chat(
            messages
        )


        print(
            "EXECUTOR RESPONSE:",
            response
        )



        #
        # Parse JSON
        #

        try:

            data = extract_json(
                response
            )


        except Exception as e:


            print(
                "JSON PARSE FAILED:",
                e
            )


            messages.append(
                {
                    "role": "assistant",
                    "content": response
                }
            )


            messages.append(
                {
                    "role": "user",
                    "content": """
Your previous response was invalid.

Return ONLY JSON.

Tool call:

{
 "tool":"tool_name",
 "args":{}
}


OR final response:

{
 "final_answer":"actual answer"
}
"""
                }
            )


            continue



        #
        # Model incorrectly says decision=final_answer
        #

        if data.get("decision") == "final_answer":


            messages.append(

                {
                    "role": "user",
                    "content": """
Investigation is complete.

Now write the final answer.

Return ONLY:

{
 "final_answer":"actual detailed response"
}

Do not return:
- decision
- reason
- placeholders
"""
                }

            )


            continue



        #
        # Completed
        #

        if (
            validate_agent_response(data)
            and "final_answer" in data
        ):


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
                    "role": "user",
                    "content": """
No valid action detected.

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



        #
        # Execute tool
        #

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



        #
        # Save observation
        #

        state.add_tool(
            tool,
            result
        )



        #
        # Serialize result for LLM
        #

        tool_result = json.dumps(
            result,
            indent=2,
            default=str
        )



        #
        # Continue reasoning
        #

        messages.append(

            {
                "role": "assistant",
                "content": response
            }

        )


        messages.append(

            {
                "role": "user",
                "content": f"""
Tool executed:

{tool}


Tool result:

{tool_result}


Continue.


Decide:

1. Need another tool?
Return:

{{
 "tool":"tool_name",
 "args":{{}}
}}


2. Investigation complete?
Return:

{{
 "final_answer":"actual complete answer"
}}


Do not use placeholders.

Return ONLY JSON.
"""
            }

        )



    return {

        "final_answer":
            "Maximum execution steps reached before completion."

    }