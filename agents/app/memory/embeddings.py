import os

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")


EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


def create_embedding(text: str) -> list[float]:

    if not text or not text.strip():
        raise ValueError("Cannot create embedding for empty text")

    response = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    return data["embedding"]
