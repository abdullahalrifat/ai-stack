import json
from pathlib import Path
from unittest import mock

import app.tools.register  # noqa: F401  (registers tools into the real registry)
import pytest
from app.tools.registry import registry as real_registry
from evals.replay import replay_trace
from evals.traces import (
    model_turns_from_transcript,
    tool_sequence_from_transcript,
    trace_from_transcript,
    validate,
)

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "evals" / "golden"


class FakeRegistry:
    def __init__(self, results):
        self.tools = list(real_registry.list_tools())
        self.results = results
        self.calls = []

    def list_tools(self):
        return self.tools

    def execute(self, name, args):
        self.calls.append({"tool": name, "args": dict(args or {})})
        result = self.results.get(name)
        return result(args) if callable(result) else result


def load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))


def registry_for(trace: dict) -> FakeRegistry:
    return FakeRegistry(
        {item["tool"]: item["result"] for item in trace["tool_sequence"]}
    )


def test_golden_traces_validate():
    for path in GOLDEN_DIR.glob("*.json"):
        trace = json.loads(path.read_text(encoding="utf-8"))
        assert validate(trace) == [], path.name


def test_validate_rejects_missing_keys():
    problems = validate({"id": "x"})
    assert "missing required key: prompt" in problems


def test_validate_rejects_tool_script_mismatch():
    trace = load_golden("code-review-read-only.json")
    trace["model_turns"][0]["tool_calls"][0]["name"] = "search_text"
    assert any("no tool_sequence record" in problem for problem in validate(trace))


def test_transcript_reconstruction_round_trips():
    trace = load_golden("fix-and-verify.json")
    reconstructed = trace_from_transcript(
        _transcript_from_trace(trace),
        run_id="roundtrip",
        prompt=trace["prompt"],
        profile=trace["profile"],
        model=trace["model"],
        workspace=trace["workspace"],
        allow_write=trace["allow_write"],
        plan=trace["plan"],
        answer=trace["answer"],
        steps=trace["steps"],
        prefetch_calls=trace["tool_sequence"][:4],
    )
    assert reconstructed["tool_sequence"] == trace["tool_sequence"]
    assert reconstructed["model_turns"] == trace["model_turns"]


def _transcript_from_trace(trace: dict) -> list[dict]:
    """Build the executor-shaped transcript a golden trace was recorded from."""

    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": trace["prompt"]},
    ]
    for turn in trace["model_turns"]:
        calls = turn.get("tool_calls")
        if calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                        for call in calls
                    ],
                }
            )
            for call in calls:
                result = next(
                    item["result"]
                    for item in trace["tool_sequence"]
                    if item["tool"] == call["name"]
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result),
                    }
                )
        else:
            messages.append({"role": "assistant", "content": turn["content"]})
    return messages


def test_model_turns_exclude_rejected_drafts():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "draft rejected"},
        {"role": "user", "content": "repair: make the edit"},
        {"role": "assistant", "content": "final accepted"},
    ]
    assert model_turns_from_transcript(messages) == [{"content": "final accepted"}]


def test_tool_sequence_pairs_parallel_calls_by_id():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "a",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"file_path": "a.py"}',
                    },
                },
                {
                    "id": "b",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"file_path": "b.py"}',
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": "b", "content": '{"path": "b.py"}'},
        {"role": "tool", "tool_call_id": "a", "content": '{"path": "a.py"}'},
    ]
    sequence = tool_sequence_from_transcript(messages)
    assert [item["tool"] for item in sequence] == ["read_file", "read_file"]
    assert sequence[0]["result"] == {"path": "a.py"}
    assert sequence[1]["result"] == {"path": "b.py"}


@pytest.mark.parametrize("golden_name", ["code-review-read-only.json"])
def test_replay_passes_on_matching_read_only_trace(golden_name):
    trace = load_golden(golden_name)
    fake = registry_for(trace)
    with mock.patch("app.agent.executor.registry", fake):
        report = replay_trace(trace, workspace=".")
    assert report["status"] == "PASS", report["reasons"]
    assert report["golden_tool_calls"] == report["observed_tool_calls"]
    assert report["graph_phase"] == "complete"
    assert report["graph_history"][0]["to"] == "analyzing"


def test_replay_passes_on_matching_write_trace():
    trace = load_golden("fix-and-verify.json")
    fake = registry_for(trace)
    with mock.patch("app.agent.executor.registry", fake):
        report = replay_trace(trace, workspace=".")
    assert report["status"] == "PASS", report["reasons"]
    assert report["golden_tool_calls"] == report["observed_tool_calls"]
    assert report["graph_phase"] == "reviewing"
    assert any(item["to"] == "verifying" for item in report["graph_history"])


def test_replay_fails_when_model_script_is_shorter_than_golden():
    trace = load_golden("code-review-read-only.json")
    trace["model_turns"] = trace["model_turns"][:1]
    fake = registry_for(trace)
    with mock.patch("app.agent.executor.registry", fake):
        report = replay_trace(trace, workspace=".")
    assert report["status"] == "FAIL"
    assert any("script" in reason for reason in report["reasons"])


def test_replay_fails_on_tool_failure_divergence():
    trace = load_golden("code-review-read-only.json")
    fake = FakeRegistry(
        {
            "list_files": {"error": "workspace unavailable"},
            "read_file": {"error": "permission denied"},
        }
    )
    with mock.patch("app.agent.executor.registry", fake):
        report = replay_trace(trace, workspace=".")
    assert report["status"] == "FAIL"
    assert report["reasons"]


def test_replay_rejects_invalid_trace_without_running():
    report = replay_trace({"id": "bad"})
    assert report["status"] == "FAIL"
    assert "invalid trace" in report["reasons"]
