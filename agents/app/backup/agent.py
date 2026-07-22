import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


from langchain.agents.factory import create_agent
from langchain.chat_models import init_chat_model


from agents.app.backup.tools.register import TOOLS


from agents.app.backup.memory.memory import (
    get_conversation,
    save_conversation,
    search_long_term_memory,
    save_long_term_memory,
)


from agents.app.backup.memory.embeddings import (
    create_embedding
)



OPENAI_API_BASE = os.getenv(
    "OPENAI_API_BASE",
    "http://litellm:4000/v1"
)

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    ""
)


DEFAULT_CONVERSATION_ID = "default"

MAX_HISTORY_ITEMS = 20



_llm = None
_agent = None



# =====================================================
# LLM
# =====================================================

def get_llm():

    global _llm


    if _llm is None:

        _llm = init_chat_model(

            model="openai:qwen3-8b",

            openai_api_base=OPENAI_API_BASE,

            openai_api_key=OPENAI_API_KEY,

            temperature=0,

        )


    return _llm




# =====================================================
# Agent
# =====================================================
SYSTEM_PROMPT = """
You are my private AI engineering assistant.

You are running inside my homelab.

Your job is to help with software engineering by actively inspecting the workspace before answering.

Available tools:

- list_files
- read_file
- search_files
- list_docker_containers
- docker_logs
- restart_container

When the user asks questions like:

- "Analyze this project"
- "Review my code"
- "Explain this repository"
- "Find bugs"
- "How does this project work?"

you MUST inspect the workspace using the filesystem tools.

Never say you cannot access the files unless every filesystem tool has failed.

When reviewing code:

1. Discover the project structure.
2. Read important files.
3. Understand dependencies.
4. Explain architecture.
5. Suggest improvements.

Do not hallucinate project contents.

Always inspect first, answer second.
"""

def build_agent():

    return create_agent(

        model=get_llm(),

        tools=TOOLS,

        system_prompt=SYSTEM_PROMPT,

        name="local-ai-agent",

    )




def get_agent():

    global _agent


    if _agent is None:

        _agent = build_agent()


    return _agent




# =====================================================
# History formatting
# =====================================================

def format_history(history):

    messages=[]


    for item in history:

        if not isinstance(item,dict):
            continue


        role=item.get(
            "role",
            "user"
        )


        content=item.get(
            "content"
        )


        if not isinstance(content,str):
            continue


        if not content.strip():
            continue


        messages.append(
            f"{role}: {content}"
        )


    return "\n".join(messages)





# =====================================================
# Agent execution
# =====================================================

def run_agent(
    message:str,
    conversation_id:str
):


    history=get_conversation(
        conversation_id
    )


    history_context=format_history(
        history
    )


    #
    # Retrieve semantic memory
    #

    embedding=create_embedding(
        message
    )


    memories=search_long_term_memory(
        embedding
    )



    prompt=f"""

Conversation:

{history_context}


Relevant memory:

{memories}


Current user request:

{message}

"""


    agent=get_agent()



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



    if isinstance(result,dict) and "messages" in result:

        answer=result["messages"][-1].content

    else:

        answer=str(result)



    #
    # Save short memory
    #

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





# =====================================================
# Public API
# =====================================================

def chat(
    message:str,
    conversation_id:Optional[str]=None
):

    if not message or not message.strip():

        raise ValueError(
            "Message cannot be empty"
        )


    conversation_id = (
        conversation_id
        or DEFAULT_CONVERSATION_ID
    )


    return run_agent(
        message,
        conversation_id
    )





# =====================================================
# Document ingestion
# =====================================================

def ingest_documents(
    texts:List[str],
    metadata:Optional[Dict[str,Any]]=None
):

    stored=0


    for text in texts:

        if text and text.strip():

            embedding=create_embedding(
                text
            )


            save_long_term_memory(

                text,

                embedding,

                metadata or {}

            )


            stored+=1



    return {

        "stored":stored,

        "status":"success"

    }