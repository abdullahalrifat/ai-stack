from types import SimpleNamespace

from app.agent.shared_runtime import ServerAgentRuntime


def test_runtime_reuses_state_and_builds_delta():
    state = SimpleNamespace(
        user_message="fix it",
        history=[],
        plan=["inspect"],
        successful_mutation_paths=set(),
        successful_verification=False,
    )
    runtime = ServerAgentRuntime.for_state(state)
    assert ServerAgentRuntime.for_state(state) is runtime
    first = runtime.delta(state)
    assert first["changed"]["plan"] == ["inspect"]
    assert runtime.delta(state)["changed"] == {}


def test_large_tool_output_uses_artifact():
    runtime = ServerAgentRuntime.for_state(SimpleNamespace())
    result = runtime.summarize("command", "x" * 10000)
    assert "artifact://sha256/" in result


def test_turn_reservation_is_committed_once():
    runtime = ServerAgentRuntime.for_state(SimpleNamespace())
    reservation = runtime.reserve_turn("implementer", ["hello"], [], 100)
    runtime.record_turn(
        reservation,
        "implementer",
        "model",
        ["hello"],
        [],
        "answer",
        {"input_tokens": 7, "output_tokens": 3},
    )
    totals = runtime.ledger.totals(include_reserved=True)
    assert totals.input_tokens == 7
    assert totals.output_tokens == 3


def test_failed_turn_reservation_can_be_refunded():
    runtime = ServerAgentRuntime.for_state(SimpleNamespace())
    reservation = runtime.reserve_turn("implementer", ["hello"], [], 100)
    runtime.refund_turn(reservation)
    totals = runtime.ledger.totals(include_reserved=True)
    assert totals.input_tokens == 0
    assert totals.output_tokens == 0


def test_runtime_artifacts_are_retrievable():
    runtime = ServerAgentRuntime.for_state(SimpleNamespace())
    summary = runtime.summarize("command", "x" * 10000)
    import json

    uri = json.loads(summary)["artifact"]["uri"]
    result = runtime.read_artifact(uri, offset=10, limit=5)
    assert result["content"] == "xxxxx"
