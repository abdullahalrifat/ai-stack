import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import redis
from langchain.agents.factory import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.documents import Document
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain.tools import tool
from langchain_qdrant import Qdrant
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams


QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "agent_memory")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "http://litellm:4000/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_CONVERSATION_ID = "default"
MAX_HISTORY_ITEMS = 20

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

_llm: Optional[Any] = None
_embeddings: Optional[Any] = None
_vector_store: Optional[Qdrant] = None


def get_llm() -> Any:
    global _llm
    if _llm is None:
        _llm = init_chat_model(
            model="openai:qwen3-8b",
            openai_api_base=OPENAI_API_BASE,
            openai_api_key=OPENAI_API_KEY,
            temperature=0,
        )
    return _llm


def get_embeddings() -> Any:
    global _embeddings
    if _embeddings is None:
        _embeddings = OpenAIEmbeddings(
            model="embedding",
            openai_api_base=OPENAI_API_BASE,
            openai_api_key=OPENAI_API_KEY,
        )
    return _embeddings

_vector_store: Optional[Qdrant] = None


def _ensure_collection(client: QdrantClient) -> None:
    collection_name = QDRANT_COLLECTION
    existing = []
    try:
        existing = [collection.name for collection in client.get_collections().collections]
    except Exception:
        existing = []

    if collection_name in existing:
        return

    vector_sample = get_embeddings().embed_query("initialize long-term memory collection")
    client.recreate_collection(
        collection_name=collection_name,
        vectors_config={
            "vectors": VectorParams(size=len(vector_sample), distance=Distance.COSINE)
        },
    )


def get_vector_store() -> Qdrant:
    global _vector_store
    if _vector_store is not None:
        return _vector_store

    client = QdrantClient(url=QDRANT_URL, prefer_grpc=False)
    _ensure_collection(client)
    _vector_store = Qdrant(client=client, collection_name=QDRANT_COLLECTION, embeddings=get_embeddings())
    return _vector_store


def _conversation_key(conversation_id: str) -> str:
    return f"agent:conversation:{conversation_id}"


def load_conversation(conversation_id: Optional[str]) -> List[Dict[str, str]]:
    conversation_id = conversation_id or DEFAULT_CONVERSATION_ID
    raw = redis_client.get(_conversation_key(conversation_id))
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def save_conversation(conversation_id: Optional[str], history: List[Dict[str, str]]) -> None:
    conversation_id = conversation_id or DEFAULT_CONVERSATION_ID
    history = history[-MAX_HISTORY_ITEMS:]
    redis_client.set(_conversation_key(conversation_id), json.dumps(history))


def _format_history(history: List[Dict[str, str]]) -> str:
    rows = []
    for entry in history:
        role = entry.get("role", "user")
        content = entry.get("content", "").strip()
        if not content:
            continue
        label = {
            "assistant": "Assistant",
            "user": "User",
            "system": "System",
        }.get(role, "User")
        rows.append(f"{label}: {content}")
    return "\n".join(rows)


def search_long_term_memory(query: str, top_k: int = 4) -> str:
    vector_store = get_vector_store()
    documents = vector_store.similarity_search(query, k=top_k)
    if not documents:
        return "No relevant long-term memory documents were found."

    formatted_documents = []
    for index, document in enumerate(documents, start=1):
        metadata = document.metadata or {}
        metadata_str = ", ".join(f"{key}={value}" for key, value in metadata.items())
        formatted_documents.append(
            f"[{index}] {document.page_content.strip()}\nMetadata: {metadata_str}"
        )
    return "\n\n".join(formatted_documents)


def store_long_term_memory(text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    text = text.strip()
    if not text:
        return "No text provided to store."

    metadata = metadata or {}
    metadata.setdefault("source", "agent")
    metadata.setdefault("created_at", datetime.utcnow().isoformat())

    document = Document(page_content=text, metadata=metadata)
    get_vector_store().add_documents([document])
    return "Stored 1 document in long-term memory."


@tool(
    "LongTermMemorySearch",
    description=(
        "Search the long-term memory vector store to retrieve relevant knowledge, past decisions, "
        "and previously stored documents. Use this tool when the user asks about stored knowledge or "
        "referencing prior conversations."
    ),
)
def long_term_memory_search(query: str) -> str:
    return search_long_term_memory(query)


@tool(
    "StoreMemory",
    description=(
        "Store important facts, summaries, or decisions into the long-term memory store. "
        "Use this tool when the user provides new information that should be remembered for later."
    ),
)
def store_memory_tool(text: str) -> str:
    return store_long_term_memory(text)


def _build_agent() -> Any:
    system_prompt = (
        "You are a local AI assistant running inside a private self-hosted environment. "
        "You have access to tools for searching and storing long-term memory. "
        "Always provide a helpful answer and use the tools when you need to recall or preserve facts across conversations."
    )

    return create_agent(
        get_llm(),
        [long_term_memory_search, store_memory_tool],
        system_prompt=system_prompt,
        name="local-rag-agent",
    )


def _run_agent_with_prompt(prompt: str, conversation_id: Optional[str] = None) -> str:
    history = load_conversation(conversation_id)
    agent = _build_agent()
    context = _format_history(history)
    full_input = f"Conversation history:\n{context}\n\n{prompt}" if context else prompt

    result = agent.invoke({"input": full_input})
    # Extract the output from the result dict (LangGraph agents return a dict with "output" key)
    answer = result.get("output") if isinstance(result, dict) else str(result)
    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": answer})
    save_conversation(conversation_id, history)
    return answer


def chat(message: str, conversation_id: Optional[str] = None) -> str:
    if not message or not message.strip():
        raise ValueError("The message must contain text.")

    prompt = f"User: {message}"
    return _run_agent_with_prompt(prompt, conversation_id)


def ingest_documents(texts: List[str], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    stored = 0
    for text in texts:
        if text and text.strip():
            store_long_term_memory(text, metadata=metadata)
            stored += 1
    return {"stored": stored, "collection": QDRANT_COLLECTION}