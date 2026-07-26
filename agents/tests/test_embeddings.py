from unittest.mock import Mock

from app.memory import embeddings


def test_bounded_embedding_text_preserves_head_and_tail():
    text = "QUESTION\n" + ("middle " * 2_000) + "\nFINAL ANSWER"

    bounded = embeddings._bounded_embedding_text(text, limit=1_000)

    assert bounded.startswith("QUESTION")
    assert bounded.endswith("FINAL ANSWER")
    assert len(bounded) < 1_100


def test_create_embedding_sends_bounded_prompt(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"embedding": [0.1, 0.2]}
    post = Mock(return_value=response)
    monkeypatch.setattr(embeddings._session, "post", post)
    monkeypatch.setattr(embeddings, "EMBED_MAX_CHARS", 100)

    result = embeddings.create_embedding("x" * 1_000)

    assert result == [0.1, 0.2]
    assert len(post.call_args.kwargs["json"]["prompt"]) < 200
