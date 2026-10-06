from unittest.mock import Mock

from app.memory import embeddings


def test_bounded_embedding_text_preserves_head_and_tail():
    text = "QUESTION\n" + ("middle " * 2_000) + "\nFINAL ANSWER"

    bounded = embeddings._bounded_embedding_text(text, limit=1_000)

    assert bounded.startswith("QUESTION")
    assert bounded.endswith("FINAL ANSWER")
    assert len(bounded) < 1_100


def test_create_embedding_sends_bounded_input_to_inference_gateway(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [{"embedding": [0.1, 0.2], "index": 0}],
        "model": "nomic-embed-text",
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(embeddings._session, "post", post)
    monkeypatch.setattr(embeddings, "INFERENCE_BASE_URL", "http://inference:8080/v1")
    monkeypatch.setattr(embeddings, "INFERENCE_API_KEY", "secret")
    monkeypatch.setattr(embeddings, "EMBED_MAX_CHARS", 100)

    result = embeddings.create_embedding("x" * 1_000)

    assert result == [0.1, 0.2]
    assert post.call_args.args[0] == "http://inference:8080/v1/embeddings"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret"
    assert len(post.call_args.kwargs["json"]["input"]) < 200


def test_create_embedding_requires_gateway(monkeypatch):
    monkeypatch.setattr(embeddings, "INFERENCE_BASE_URL", "")
    try:
        embeddings.create_embedding("hello")
    except RuntimeError as exc:
        assert "INFERENCE_BASE_URL" in str(exc)
    else:
        raise AssertionError("expected missing inference gateway to fail")
