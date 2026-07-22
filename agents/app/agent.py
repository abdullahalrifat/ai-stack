import uuid

from typing import List, Dict, Any, Optional


from .state import AgentState

from .planner import create_plan

from .executor import execute_plan

from .memory import (
    get_conversation,
    save_conversation,
    search_memory,
    save_memory,
    save_long_term_memory,
)

from .memory.embeddings import create_embedding





def run_agent(
    message: str,
    conversation_id: str | None = None,
    workspace="/workspace"
):

    conversation_id = (
        conversation_id
        or str(uuid.uuid4())
    )


    state = AgentState(

        conversation_id=conversation_id,

        user_message=message

    )

    state.workspace = workspace

    #
    # Load short memory
    #

    state.history = get_conversation(

        conversation_id

    )



    #
    # Load semantic memory
    #

    state.memories = search_memory(

        message

    )



    #
    # Create execution plan
    #

    state.plan = create_plan(

        state

    )



    #
    # Execute
    #

    answer = execute_plan(

        state

    )



    state.answer = answer



    #
    # Save conversation
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



    #
    # Save long-term memory
    #

    save_memory(

        message,

        answer

    )



    return {

        "conversation_id":

            conversation_id,


        "answer":

            answer

    }





# =====================================================
# Document ingestion
# =====================================================


def ingest_documents(

    texts: List[str],

    metadata: Optional[
        Dict[str, Any]
    ] = None

):

    stored = 0



    for text in texts:


        if not text or not text.strip():

            continue



        embedding = create_embedding(

            text

        )



        save_long_term_memory(

            text,

            embedding,

            metadata or {}

        )



        stored += 1



    return {

        "stored":

            stored,


        "status":

            "success"

    }