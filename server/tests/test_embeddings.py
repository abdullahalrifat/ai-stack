from unittest.mock import Mock

from app.memory import embeddings


def test_bounded_embedding_text_preserves_head_and_tail():
    text = "QUESTION\n" + ("middle " * 2_000) + "\nFINAL ANSWER"

    bounded = embeddings._bounded_embedding_text(text, limit=1_000)

    assert bounded.startswith("QUESTION")
    assert bounded.endswith("FINAL ANSWER")
    assert len(bounded) < 1_100


def test_create_embedding_uses_shared_core_client(monkeypatch):
    core_client = Mock()
    core_client.embeddings.return_value = {
        "data": [{"embedding": [0.1, 0.2], "index": 0}],
        "model": "nomic-embed-text",
    }
    monkeypatch.setattr(embeddings, "_get_client", lambda: core_client)
    monkeypatch.setattr(embeddings, "INFERENCE_BASE_URL", "http://inference:8080/v1")
    monkeypatch.setattr(embeddings, "INFERENCE_API_KEY", "secret")
    monkeypatch.setattr(embeddings, "EMBED_MAX_CHARS", 100)

    result = embeddings.create_embedding("x" * 1_000)

    assert result == [0.1, 0.2]
    kwargs = core_client.embeddings.call_args.kwargs
    assert kwargs["model"] == "nomic-embed-text"
    assert kwargs["encoding_format"] == "float"
    assert kwargs["timeout"] == embeddings.EMBEDDING_TIMEOUT_SECONDS
    assert len(kwargs["inputs"]) < 200


def test_create_embedding_requires_gateway(monkeypatch):
    monkeypatch.setattr(embeddings, "INFERENCE_BASE_URL", "")
    try:
        embeddings.create_embedding("hello")
    except RuntimeError as exc:
        assert "INFERENCE_BASE_URL" in str(exc)
    else:
        raise AssertionError("expected missing inference gateway to fail")
