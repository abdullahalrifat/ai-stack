from openai import OpenAI
import os


client = OpenAI(

    base_url=os.getenv(
        "OPENAI_API_BASE"
    ),

    api_key=os.getenv(
        "OPENAI_API_KEY"
    )

)



def chat(messages):

    response = client.chat.completions.create(

        model=os.getenv(
            "MODEL",
            "qwen3-8b"
        ),

        messages=messages,

        temperature=0

    )


    return (
        response
        .choices[0]
        .message
        .content
    )