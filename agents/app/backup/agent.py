import os
from typing import Any, Dict, List, Optional


from langchain.agents.factory import create_agent
from langchain.chat_models import init_chat_model


from app.tools.register import TOOLS


from app.memory.memory import (
    get_conversation,
    save_conversation,
    search_long_term_memory,
    save_long_term_memory,
)


from app.memory.embeddings import create_embedding



OPENAI_API_BASE=os.getenv(
    "OPENAI_API_BASE",
    "http://litellm:4000/v1"
)


OPENAI_API_KEY=os.getenv(
    "OPENAI_API_KEY",
    ""
)


DEFAULT_CONVERSATION_ID="default"



def get_llm():

    return init_chat_model(

        model="openai:qwen3-8b",

        openai_api_base=OPENAI_API_BASE,

        openai_api_key=OPENAI_API_KEY,

        temperature=0,

    )



SYSTEM_PROMPT="""

You are a private software engineering agent.

You MUST inspect files before answering.

Workflow:

1. Understand workspace.
2. Use filesystem tools.
3. Read important files.
4. Analyze architecture.
5. Report findings.

Never invent files.

Never claim analysis without tool usage.

"""



def build_agent():

    return create_agent(

        model=get_llm(),

        tools=TOOLS,

        system_prompt=SYSTEM_PROMPT,

        name="engineering-agent"

    )




def format_history(history):

    result=[]

    for item in history:

        if not isinstance(item,dict):
            continue


        content=item.get("content")

        if content:

            result.append(
                content
            )


    return "\n".join(result)





def run_agent(
    message:str,
    conversation_id:str,
    workspace:str="/workspace"
):


    history=format_history(

        get_conversation(
            conversation_id
        )

    )



    embedding=create_embedding(
        message
    )


    memories=search_long_term_memory(
        embedding
    )



    prompt=f"""

Workspace:

{workspace}


Previous conversation:

{history}


Memory:

{memories}


User request:

{message}


IMPORTANT:

When using filesystem tools:

Always use:

{workspace}

as the root directory.

Inspect first.

Answer only after investigation.

"""



    # IMPORTANT
    # New agent per request

    agent=build_agent()



    result=agent.invoke(

        {
            "messages":
            [
                {
                    "role":"user",
                    "content":prompt
                }
            ]
        }

    )



    if isinstance(result,dict):

        messages=result.get(
            "messages",
            []
        )


        if messages:

            answer=messages[-1].content

        else:

            answer=str(result)


    else:

        answer=str(result)



    save_conversation(
        conversation_id,
        "user",
        message
    )


    save_conversation(
        conversation_id,
        "assistant",
        answer
    )



    return answer




def chat(
    message:str,
    conversation_id:Optional[str]=None,
    workspace:str="/workspace"
):


    return run_agent(

        message,

        conversation_id
        or DEFAULT_CONVERSATION_ID,

        workspace

    )





def ingest_documents(
    texts:List[str],
    metadata:Optional[Dict[str,Any]]=None
):


    count=0


    for text in texts:


        embedding=create_embedding(
            text
        )


        save_long_term_memory(

            text,

            embedding,

            metadata or {}

        )


        count+=1



    return {

        "stored":count

    }