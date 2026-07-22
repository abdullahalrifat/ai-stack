from .llm import chat

from .parser import (
    extract_json,
    parse_tool_arguments
)

from .tool_registry import registry

from .prompts import EXECUTOR_PROMPT



MAX_STEPS = 15




def execute_plan(state):


    available_tools = registry.list_tools()



    messages = [

        {
            "role": "system",
            "content": EXECUTOR_PROMPT
        },


        {
            "role": "user",
            "content": f"""

Task:

{state.user_message}



Plan:

{state.plan}



Available tools:

{available_tools}



Previous observations:

{state.observations}

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



        data = extract_json(

            response

        )



        #
        # Final answer
        #

        if "final_answer" in data:


            state.finished = True


            return data["final_answer"]




        #
        # Tool call
        #

        tool = data.get(
            "tool"
        )



        if not tool:

            return response




        args = parse_tool_arguments(

            data.get(
                "args"
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
                    "tool",

                "name":
                    tool,

                "content":
                    str(result)

            }

        )



        # Tell model about updated state

        messages.append(

            {

                "role":
                    "user",

                "content":
                f"""
Observation received:

{result}


Continue.
"""

            }

        )




    return "Maximum execution steps reached"