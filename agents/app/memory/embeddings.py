import os

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")


EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EMBED_MAX_CHARS = int(os.getenv("OLLAMA_EMBED_MAX_CHARS", "6000"))


# Reused across calls for connection pooling instead of opening a new
# connection on every embedding request.
_session = requests.Session()


def _bounded_embedding_text(text: str, limit: int = EMBED_MAX_CHARS) -> str:
    """Fit embedding input below the local model's physical 2K-token batch."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = limit * 3 // 5
    tail = limit - head
    return f"{text[:head]}\n...[embedding input omitted]...\n{text[-tail:]}"


def create_embedding(text: str) -> list[float]:

    if not text or not text.strip():
        raise ValueError("Cannot create embedding for empty text")
    bounded_text = _bounded_embedding_text(text, EMBED_MAX_CHARS)

    response = _session.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": bounded_text},
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    return data["embedding"]
