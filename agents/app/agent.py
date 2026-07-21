from langgraph.graph import StateGraph
from langchain_openai import ChatOpenAI
import os
from typing import TypedDict


class AgentState(TypedDict):
    messages:list


llm = ChatOpenAI(
    model="qwen3-8b",
    temperature=0,
    base_url=os.getenv("OPENAI_API_BASE"),
    api_key=os.getenv("OPENAI_API_KEY"),
)


def chatbot(state):

    response = llm.invoke(
        state["messages"]
    )

    return {
        "messages":
        state["messages"]
        +
        [
            response
        ]
    }



workflow = StateGraph(
    AgentState
)


workflow.add_node(
    "chatbot",
    chatbot
)


workflow.set_entry_point(
    "chatbot"
)


workflow.set_finish_point(
    "chatbot"
)


agent = workflow.compile()