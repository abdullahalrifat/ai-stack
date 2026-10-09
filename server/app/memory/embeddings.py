"""OpenAI-compatible embeddings via the dedicated inference gateway."""

from __future__ import annotations

import os

from jarvis_core import InferenceClient, InferenceConfig

INFERENCE_BASE_URL = os.getenv("INFERENCE_BASE_URL", "").rstrip("/")
INFERENCE_API_KEY = os.getenv("INFERENCE_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
EMBED_MAX_CHARS = int(os.getenv("EMBED_MAX_CHARS", "6000"))
EMBEDDING_TIMEOUT_SECONDS = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "45"))

_client: InferenceClient | None = None


def _get_client() -> InferenceClient:
    global _client
    if _client is None:
        _client = InferenceClient(
            InferenceConfig(
                base_url=INFERENCE_BASE_URL,
                api_key=INFERENCE_API_KEY,
                timeout=EMBEDDING_TIMEOUT_SECONDS,
                user_agent="ai-stack-embeddings",
            )
        )
    return _client


def _bounded_embedding_text(text: str, limit: int = EMBED_MAX_CHARS) -> str:
    """Keep embedding input bounded before sending it to the inference VM."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = limit * 3 // 5
    tail = limit - head
    return f"{text[:head]}\n...[embedding input omitted]...\n{text[-tail:]}"


def create_embedding(text: str) -> list[float]:
    if not text or not text.strip():
        raise ValueError("Cannot create embedding for empty text")
    if not INFERENCE_BASE_URL:
        raise RuntimeError("INFERENCE_BASE_URL is required for embeddings")

    bounded_text = _bounded_embedding_text(text, EMBED_MAX_CHARS)
    data = _get_client().embeddings(
        model=EMBED_MODEL,
        inputs=bounded_text,
        encoding_format="float",
        timeout=EMBEDDING_TIMEOUT_SECONDS,
    )
    items = data.get("data") or []
    if not items:
        raise RuntimeError("Inference gateway returned no embedding")
    return [float(value) for value in items[0]["embedding"]]
