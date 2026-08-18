from app.tools import code_intelligence, filesystem
from app.tools.filesystem import workspace_context


def test_analyze_task_context_returns_symbols_dependencies_and_tests(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(filesystem, "WORKSPACE_ROOTS", [tmp_path])
    source = workspace / "app" / "dispatcher.py"
    source.parent.mkdir()
    source.write_text(
        "import asyncio\n\nasync def dispatch_experts(tasks):\n    return await asyncio.gather(*tasks)\n",
        encoding="utf-8",
    )
    test = workspace / "tests" / "test_dispatcher.py"
    test.parent.mkdir()
    test.write_text(
        "from app.dispatcher import dispatch_experts\n\ndef test_dispatch_experts(): pass\n",
        encoding="utf-8",
    )

    with workspace_context(str(workspace)):
        result = code_intelligence.analyze_task_context.invoke(
            {"requirement": "multi expert parallel dispatch"}
        )

    assert result["relevant_files"] == ["app/dispatcher.py"]
    assert result["test_targets"] == ["tests/test_dispatcher.py"]
    assert result["owning_symbols"][0]["name"] == "dispatch_experts"
    assert result["dependencies"]["app/dispatcher.py"] == ["asyncio"]


def test_inspect_code_batches_symbol_pattern_and_range_requests(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(filesystem, "WORKSPACE_ROOTS", [tmp_path])
    source = workspace / "worker.py"
    source.write_text(
        "import os\n\ndef first():\n    return 1\n\ndef second():\n    return first()\n",
        encoding="utf-8",
    )

    with workspace_context(str(workspace)):
        result = code_intelligence.inspect_code.invoke(
            {
                "requests": [
                    {"path": "worker.py", "symbol": "second"},
                    {"path": "worker.py", "pattern": "return first"},
                    {"path": "worker.py", "start_line": 1, "end_line": 3},
                ]
            }
        )

    assert result["items"][0]["symbol"] == "second"
    assert "return first()" in result["items"][0]["content"]
    assert result["items"][1]["matches"][0]["line"] == 7
    assert result["items"][2]["content"].startswith("import os")


def test_inspect_code_fairly_budgets_large_first_symbol(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(filesystem, "WORKSPACE_ROOTS", [tmp_path])
    source = workspace / "large.py"
    source.write_text(
        "def huge():\n"
        + "".join(f"    value_{index} = {index}\n" for index in range(2_000))
        + "    return value_1999\n\n"
        + "def focused():\n    return 42\n",
        encoding="utf-8",
    )

    with workspace_context(str(workspace)):
        result = code_intelligence.inspect_code.invoke(
            {
                "requests": [
                    {"path": "large.py", "symbol": "huge"},
                    {"path": "large.py", "symbol": "focused"},
                ]
            }
        )

    assert len(result["items"]) == 2
    assert result["items"][0]["symbol"] == "huge"
    assert result["items"][0]["truncated"] is True
    assert result["items"][1]["symbol"] == "focused"
    assert "return 42" in result["items"][1]["content"]
