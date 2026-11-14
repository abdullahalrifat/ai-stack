"""AS-5: streamed responses must contribute token metrics like non-streamed ones."""

from types import SimpleNamespace
from unittest.mock import patch

from app.llm import client, metrics
from app.llm.usage import record_usage


def _counters():
    return metrics.get_metrics()["counters"]


def test_record_usage_accepts_attribute_shape():
    before = _counters().get("llm.tokens.m1", 0)
    result = record_usage(
        "m1",
        SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    assert result["prompt_tokens"] == 10
    assert result["completion_tokens"] == 5
    assert result["total_tokens"] == 15
    assert _counters()["llm.tokens.m1"] == before + 15


def test_record_usage_accepts_mapping_shape():
    # Streaming paths (AS-5) deliver usage chunks as plain mappings once the
    # transport adapts them; record_usage must not silently record zeros.
    result = record_usage(
        "m2",
        {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    )
    assert result["prompt_tokens"] == 7
    assert result["completion_tokens"] == 3
    assert result["total_tokens"] == 10


def test_record_usage_prefers_openai_names_over_anthropic_aliases():
    result = record_usage(
        "m3",
        {"prompt_tokens": 4, "input_tokens": 100, "completion_tokens": 2},
    )
    assert result["prompt_tokens"] == 4
    assert result["completion_tokens"] == 2
    assert result["total_tokens"] == 6


class FakeStreamClient:
    """InferenceClient-shaped double whose stream() yields raw chunk dicts."""

    name = "fake"

    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._chunks)


def test_stream_create_records_final_usage_chunk():
    chunks = [
        {"choices": [{"delta": {"content": "hi"}}], "finish_reason": None},
        # OpenAI include_usage semantics: terminal usage-only chunk.
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 7,
                "total_tokens": 12,
            },
        },
    ]
    fake = FakeStreamClient(chunks)
    completions = client._CoreCompletions(fake)

    before = _counters().get("llm.tokens.fake-model", 0)
    out = list(
        completions.create(
            model="fake-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
        )
    )

    # All chunks flow through untouched, including the usage-only one.
    assert len(out) == 2
    assert not out[1].choices
    assert out[1].usage.total_tokens == 12
    # The gateway was asked for a final usage chunk.
    assert fake.calls[0]["stream_options"] == {"include_usage": True}
    # And the usage was recorded exactly once.
    assert _counters()["llm.tokens.fake-model"] == before + 12
    assert _counters()["llm.requests.fake-model"] >= 1


def test_stream_create_records_usage_attached_to_terminal_content_chunk():
    # Gateways that do not honor include_usage attach usage to the last
    # content chunk instead; that must be recorded too.
    chunks = [
        {"choices": [{"delta": {"content": "ok"}}]},
        {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    ]
    fake = FakeStreamClient(chunks)
    completions = client._CoreCompletions(fake)

    before = _counters().get("llm.tokens.fake-model", 0)
    list(
        completions.create(
            model="fake-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
        )
    )
    assert _counters()["llm.tokens.fake-model"] == before + 2


def test_stream_create_respects_caller_supplied_include_usage():
    fake = FakeStreamClient([{"choices": [{"delta": {"content": "x"}}]}])
    completions = client._CoreCompletions(fake)

    list(
        completions.create(
            model="fake-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            stream_options={"include_usage": False},
        )
    )
    assert fake.calls[0]["stream_options"] == {"include_usage": False}


def test_non_stream_create_does_not_double_record():
    # The usage.install() wrapper records non-streamed calls; _CoreCompletions
    # must not record them a second time (AS-5 guard against double counting).
    class FakeCompleteClient:
        name = "fake"

        def __init__(self):
            self.calls = []

        def complete(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            }

    fake = FakeCompleteClient()
    completions = client._CoreCompletions(fake)
    before = _counters().get("llm.tokens.fake-model", 0)

    response = completions.create(
        model="fake-model",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert response.choices[0].message.content == "ok"
    assert _counters()["llm.tokens.fake-model"] == before
    assert "stream_options" not in fake.calls[0]


def test_usage_install_wrapper_still_records_non_streamed_calls(monkeypatch):
    """The install() wrapper remains the recorder for the non-stream path."""
    from app.llm import client as llm_client
    from app.llm import usage

    recorded = []
    monkeypatch.setattr(
        usage, "record_usage", lambda model, u: recorded.append((model, u))
    )
    monkeypatch.setattr(usage, "_INSTALLED", False)

    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=1, completion_tokens=1, total_tokens=2
                ),
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(llm_client, "get_client", lambda: fake_client)

    usage.install()
    # The wrapper is installed on the object handed out by get_client().
    wrapped = llm_client.get_client()
    wrapped.chat.completions.create(model="m", messages=[])

    assert "stream" not in calls[0] or calls[0]["stream"] in (None, False)
    assert recorded and recorded[0][0] == "m" and recorded[0][1].total_tokens == 2
