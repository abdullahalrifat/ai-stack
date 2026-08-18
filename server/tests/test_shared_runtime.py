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
