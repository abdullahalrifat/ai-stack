from contextlib import contextmanager
from pathlib import Path

import pytest
from app.core.cancellation import cancellation_context
from app.core.exceptions import RunCancelled
from app.core.permissions import (
    FULL_WRITE,
    READ,
    SCOPED_WRITE,
    PermissionPolicy,
    permissions_context,
)
from app.tools import filesystem


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(filesystem, "WORKSPACE_ROOTS", [tmp_path])
    return tmp_path


def test_workspace_rejects_paths_outside_configured_root(workspace, tmp_path):
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)

    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(outside))


def test_workspace_rejects_symlink_escape(workspace, tmp_path):
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    escaped = workspace / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(escaped))


def test_internal_workspace_accepts_only_direct_git_sandbox(
    workspace, tmp_path, monkeypatch
):
    sandbox_root = tmp_path.parent / "sandboxes"
    valid = sandbox_root / "run-1"
    nested = sandbox_root / "nested" / "run-2"
    arbitrary = sandbox_root / "arbitrary"
    (valid / ".git").mkdir(parents=True)
    (nested / ".git").mkdir(parents=True)
    arbitrary.mkdir(parents=True)
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", sandbox_root)

    assert (
        filesystem.validate_workspace(str(valid), allow_sandbox=True) == valid.resolve()
    )
    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(valid))
    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(nested), allow_sandbox=True)
    with pytest.raises(PermissionError):
        filesystem.validate_workspace(str(arbitrary), allow_sandbox=True)


def test_request_workspace_uses_explicit_repository_in_prompt(workspace, monkeypatch):
    repository = workspace / "ai-stack"
    repository.mkdir()
    monkeypatch.setattr(filesystem, "DEFAULT_WORKSPACE", workspace)

    selected = filesystem.resolve_request_workspace(
        str(workspace), f"Review the repository at {repository}."
    )

    assert selected == str(repository)


def test_explicit_workspace_field_takes_precedence_over_prompt(workspace, monkeypatch):
    repository = workspace / "ai-stack"
    other_repository = workspace / "other"
    repository.mkdir()
    other_repository.mkdir()
    monkeypatch.setattr(filesystem, "DEFAULT_WORKSPACE", workspace)

    selected = filesystem.resolve_request_workspace(
        str(other_repository), f"Review {repository}."
    )

    assert selected == str(other_repository)


def test_resolve_path_accepts_absolute_workspace_without_leading_slash(workspace):
    repository = workspace / "ai-stack"
    repository.mkdir()
    missing_leading_slash = str(repository).lstrip("/")

    with filesystem.workspace_context(str(repository)):
        assert filesystem.resolve_path(missing_leading_slash) == repository


def test_sandbox_maps_absolute_source_paths_to_active_clone(
    workspace, tmp_path, monkeypatch
):
    source = workspace / "source"
    source.mkdir()
    sandbox_root = tmp_path.parent / "sandboxes"
    sandbox = sandbox_root / workspace.name
    (sandbox / ".git").mkdir(parents=True, exist_ok=True)
    (sandbox / "src").mkdir()
    (sandbox / "src" / "app.py").write_text("sandbox copy")
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", sandbox_root)

    with filesystem.workspace_context(
        str(sandbox), allow_sandbox=True, source_workspace=str(source)
    ):
        resolved = filesystem.resolve_path(str(source / "src" / "app.py"))
        result = filesystem.read_file.invoke(
            {"file_path": str(source / "src" / "app.py")}
        )

    assert resolved == sandbox / "src" / "app.py"
    assert result == "sandbox copy"


def test_sandbox_accepts_singular_alias_only_for_active_run(
    workspace, tmp_path, monkeypatch
):
    sandbox_root = tmp_path.parent / "sandboxes"
    sandbox = sandbox_root / workspace.name
    (sandbox / ".git").mkdir(parents=True, exist_ok=True)
    (sandbox / "agents").mkdir(exist_ok=True)
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", sandbox_root)

    with filesystem.workspace_context(str(sandbox), allow_sandbox=True):
        assert (
            filesystem.resolve_path(f"/sandbox/{sandbox.name}/agents")
            == sandbox / "agents"
        )
        with pytest.raises(PermissionError):
            filesystem.resolve_path("/sandbox/different-run/agents")


def test_list_files_tolerates_file_and_glob_arguments(workspace):
    package = workspace / "server" / "app" / "llm"
    package.mkdir(parents=True)
    init_file = package / "__init__.py"
    init_file.write_text("")

    with filesystem.workspace_context(str(workspace)):
        file_result = filesystem.list_files.invoke({"directory": str(init_file)})
        glob_result = filesystem.list_files.invoke(
            {"directory": str(package / "**init**.py")}
        )

    expected = {
        "name": "__init__.py",
        "path": "server/app/llm/__init__.py",
        "type": "file",
    }
    assert file_result == [expected]
    assert glob_result == [expected]


def test_tree_skips_runtime_state_directories(workspace):
    (workspace / "postgres").mkdir()
    (workspace / "open-webui" / "uploads").mkdir(parents=True)
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("print('ok')")

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.tree.invoke({"directory": ".", "depth": 2})

    assert "postgres" not in result
    assert "open-webui" not in result
    assert "src" in result


def test_read_tools_reject_binary_files(workspace):
    binary = workspace / "upload.pdf"
    binary.write_bytes(b"%PDF-1.7\x00binary")

    with filesystem.workspace_context(str(workspace)):
        single = filesystem.read_file.invoke({"file_path": "upload.pdf"})
        batch = filesystem.inspect_files.invoke({"paths": ["upload.pdf"]})

    assert single == {"error": "File is binary, not UTF-8 text."}
    assert batch["items"] == [
        {"path": "upload.pdf", "error": "File is binary, not UTF-8 text."}
    ]


def test_repository_tools_hide_secrets_but_allow_env_templates(workspace):
    (workspace / ".env").write_text("SECRET=do-not-read")
    (workspace / ".env.local").write_text("SECRET=also-hidden")
    (workspace / ".env.example").write_text("SECRET=placeholder")
    (workspace / "private.pem").write_text("key")

    with filesystem.workspace_context(str(workspace)):
        listing = filesystem.list_files.invoke({"directory": "."})
        denied = filesystem.read_file.invoke({"file_path": ".env"})
        template = filesystem.read_file.invoke({"file_path": ".env.example"})

    names = {item["name"] for item in listing}
    assert ".env" not in names
    assert ".env.local" not in names
    assert "private.pem" not in names
    assert ".env.example" in names
    assert denied == {
        "error": "Reading excluded files is not allowed.",
        "excluded": True,
        "reason": "credential or secret",
    }
    assert template == "SECRET=placeholder"


def test_search_code_returns_line_numbers_and_context(workspace):
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text(
        "import os\n"
        "\n"
        "def greet(name):\n"
        "    return f'hello {name}'\n"
        "\n"
        "result = greet('world')\n"
    )

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.search_code.invoke({"pattern": r"def greet"})

    assert result["truncated"] is False
    match = result["matches"][0]
    assert match["path"] == "src/main.py"
    assert match["line"] == 3
    assert match["text"] == "def greet(name):"
    assert match["context"]["before"] == ["import os", ""]
    assert match["context"]["after"] == ["    return f'hello {name}'", ""]


def test_search_code_supports_plain_substring_and_case_folding(workspace):
    (workspace / "a.py").write_text("def Handler(): pass\n")
    (workspace / "b.py").write_text("def handler(): pass\n")

    with filesystem.workspace_context(str(workspace)):
        sensitive = filesystem.search_code.invoke({"pattern": "handler"})
        folded = filesystem.search_code.invoke(
            {"pattern": "handler", "ignore_case": True}
        )

    assert [match["path"] for match in sensitive["matches"]] == ["b.py"]
    assert [match["path"] for match in folded["matches"]] == ["a.py", "b.py"]


def test_search_code_accepts_file_path_as_directory(workspace):
    """The model commonly passes a file (not a folder) as directory; that
    must search the single file instead of silently returning no matches."""
    (workspace / "src").mkdir()
    (workspace / "src" / "runner.py").write_text(
        "def cancel_job(job_id):\n    return job_id\n"
    )

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.search_code.invoke(
            {"pattern": "cancel_job", "directory": "src/runner.py"}
        )

    assert result["truncated"] is False
    assert len(result["matches"]) == 1
    assert result["matches"][0]["path"] == "src/runner.py"
    assert result["matches"][0]["text"] == "def cancel_job(job_id):"


def test_search_text_accepts_file_path_as_directory(workspace):
    (workspace / "src").mkdir()
    (workspace / "src" / "runner.py").write_text(
        "def cancel_job(job_id):\n    return job_id\n"
    )

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.search_text.invoke(
            {"keyword": "cancel_job", "directory": "src/runner.py"}
        )

    assert result == ["src/runner.py"]


def test_search_code_honors_file_glob(workspace):
    (workspace / "one.py").write_text("def target(): pass\n")
    (workspace / "one.txt").write_text("def target(): pass\n")

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.search_code.invoke(
            {"pattern": "target", "file_glob": "*.py"}
        )

    assert [match["path"] for match in result["matches"]] == ["one.py"]


def test_search_code_rejects_invalid_regex(workspace):
    with filesystem.workspace_context(str(workspace)):
        result = filesystem.search_code.invoke({"pattern": "["})

    assert "Invalid regular expression" in result["error"]


def test_search_code_skips_sensitive_and_binary_files(workspace):
    (workspace / ".env").write_text("SECRET=handler\n")
    (workspace / "blob.bin").write_bytes(b"handler\x00binary")
    (workspace / "ok.py").write_text("def handler(): pass\n")

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.search_code.invoke({"pattern": "handler"})

    assert [match["path"] for match in result["matches"]] == ["ok.py"]


def test_inspect_files_bounds_requested_paths(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_INSPECT_PATHS", 2)
    for index in range(3):
        (workspace / f"file-{index}.txt").write_text(str(index))

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.inspect_files.invoke(
            {"paths": ["file-0.txt", "file-1.txt", "file-2.txt"]}
        )

    assert result["truncated"] is True
    assert [item["path"] for item in result["items"]] == ["file-0.txt", "file-1.txt"]


def test_walk_files_prunes_ignored_subtrees_and_is_deterministic(workspace):
    (workspace / "src").mkdir()
    (workspace / "src" / "z.py").write_text("z")
    (workspace / "src" / "a.py").write_text("a")
    (workspace / "node_modules" / "package").mkdir(parents=True)
    (workspace / "node_modules" / "package" / "hidden.py").write_text("hidden")

    with filesystem.workspace_context(str(workspace)):
        paths = [filesystem.relative(path) for path in filesystem.walk_files(workspace)]

    assert paths == ["src/a.py", "src/z.py"]


def test_repository_ignore_prunes_scans_but_allows_explicit_safe_reads(workspace):
    (workspace / ".aistackignore").write_text("generated/\n*.lock\n")
    (workspace / "generated").mkdir()
    (workspace / "generated" / "client.py").write_text("generated client")
    (workspace / "dependencies.lock").write_text("safe dependency snapshot")
    (workspace / "src.py").write_text("source")

    with filesystem.workspace_context(str(workspace)):
        scanned = [
            filesystem.relative(path) for path in filesystem.walk_files(workspace)
        ]
        generated = filesystem.read_file.invoke({"file_path": "generated/client.py"})
        lockfile = filesystem.read_file.invoke({"file_path": "dependencies.lock"})
        lock_search = filesystem.search_code.invoke(
            {"directory": "dependencies.lock", "pattern": "snapshot"}
        )

    assert scanned == [".aistackignore", "src.py"]
    assert generated == "generated client"
    assert lockfile == "safe dependency snapshot"
    assert lock_search["matches"][0]["path"] == "dependencies.lock"


def test_model_artifacts_are_hard_blocked_despite_ignore_negation(workspace):
    (workspace / ".gitignore").write_text("models/\n")
    (workspace / ".aistackignore").write_text("!models/\n!models/**\n")
    (workspace / "models").mkdir()
    (workspace / "models" / "local.gguf").write_bytes(b"not-a-real-model")

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.read_file.invoke({"file_path": "models/local.gguf"})
        scanned = list(filesystem.walk_files(workspace))

    assert result["excluded"] is True
    assert result["reason"] == "model artifact"
    assert all(path.name != "local.gguf" for path in scanned)


def test_inspect_files_balances_content_across_large_files(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_TOOL_OUTPUT_CHARS", 2_600)
    (workspace / "first.md").write_text("A" * 4_000)
    (workspace / "second.md").write_text("B" * 4_000)

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.inspect_files.invoke({"paths": ["first.md", "second.md"]})

    assert len(result["items"][0]["content"]) == 550
    assert len(result["items"][1]["content"]) == 550
    assert result["items"][1]["content"].startswith("B")


def test_write_edit_and_read_stay_inside_workspace(workspace):
    with filesystem.workspace_context(str(workspace)):
        assert (
            filesystem.write_file.invoke(
                {"file_path": "src/example.txt", "content": "before"}
            )["status"]
            == "written"
        )
        assert (
            filesystem.edit_file.invoke(
                {
                    "file_path": "src/example.txt",
                    "old_string": "before",
                    "new_string": "after",
                }
            )["status"]
            == "edited"
        )
        assert filesystem.read_file.invoke({"file_path": "src/example.txt"}) == "after"
        assert (
            "Access outside workspace denied"
            in filesystem.write_file.invoke(
                {"file_path": "../outside.txt", "content": "nope"}
            )["error"]
        )


def test_run_command_enforces_policy_before_execution(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "ALLOWED_COMMANDS", ["echo"])
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda command, cwd, tier="isolated": {
            "command": command,
            "exit_code": 0,
            "output": "ok",
        },
    )

    with filesystem.workspace_context(str(workspace)):
        assert (
            "not permitted"
            in filesystem.run_command.invoke({"command": "echo ok; echo unsafe"})[
                "error"
            ]
        )
        assert (
            "not an approved command"
            in filesystem.run_command.invoke({"command": "git status"})["error"]
        )
        result = filesystem.run_command.invoke({"command": "echo ok"})

    assert result["exit_code"] == 0
    assert result["output"] == "ok"


def test_inspect_test_environment_reports_stable_coverage_capability(
    workspace, monkeypatch
):
    (workspace / "pytest.ini").write_text("[pytest]\n")
    (workspace / "app").mkdir()
    monkeypatch.setattr(filesystem.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        filesystem.importlib.util,
        "find_spec",
        lambda name: object() if name == "pytest_cov" else None,
    )

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.inspect_test_environment.invoke({"directory": "."})

    assert result["configs"] == ["pytest.ini"]
    assert result["runner"]["pytest_cov"] is True
    assert result["recommended"]["coverage_target"] == "app"
    assert result["fresh_shell_per_command"] is True


def test_inspect_test_environment_discovers_package_scoped_tests(workspace):
    (workspace / "server" / "tests").mkdir(parents=True)
    (workspace / "server" / "app").mkdir()
    (workspace / "server" / "requirements.txt").write_text("pytest\n")
    (workspace / "jarvis" / "tests").mkdir(parents=True)
    (workspace / "jarvis" / "src" / "aistack_cli").mkdir(parents=True)
    (workspace / "jarvis" / "src" / "aistack_cli" / "__init__.py").write_text("")
    (workspace / "jarvis" / "pyproject.toml").write_text("[project]\n")
    (workspace / "runs-ui" / "tests").mkdir(parents=True)
    (workspace / "runs-ui" / "package.json").write_text("{}\n")

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.inspect_test_environment.invoke({"directory": "."})

    assert result["test_directories"] == [
        "server/tests",
        "jarvis/tests",
        "runs-ui/tests",
    ]
    assert result["configs"] == ["jarvis/pyproject.toml", "runs-ui/package.json"]
    assert result["coverage_runs"] == [
        {"directory": "server", "coverage_target": "app"},
        {"directory": "jarvis", "coverage_target": "aistack_cli"},
    ]


def test_run_tests_coverage_uses_explicit_package_target(workspace, monkeypatch):
    commands = []
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda command, cwd, tier="isolated": (
            commands.append(command)
            or {"command": command, "exit_code": 0, "output": "covered"}
        ),
    )

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke(
            {
                "kind": "pytest_coverage",
                "directory": ".",
                "coverage_target": "app",
            }
        )

    assert result["exit_code"] == 0
    assert commands == ["pytest -q --cov=app --cov-report=term-missing"]


def test_run_tests_rejects_unsafe_coverage_target(workspace):
    with filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke(
            {
                "kind": "pytest_coverage",
                "coverage_target": "app; touch unsafe",
            }
        )

    assert "dotted Python package" in result["error"]


def test_isolated_runner_job_is_cancelled_with_owning_run(workspace, monkeypatch):
    class Response:
        ok = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    cancellations = []
    checks = iter([False, True])
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(
        filesystem.requests,
        "post",
        lambda url, **kwargs: (
            cancellations.append(url)
            or Response({"job_id": "job-1", "status": "cancelling"})
            if url.endswith("/cancel")
            else Response({"job_id": "job-1", "status": "running"})
        ),
    )
    monkeypatch.setattr(
        filesystem.requests,
        "get",
        lambda *_args, **_kwargs: Response({"job_id": "job-1", "status": "running"}),
    )
    monkeypatch.setattr(filesystem.time, "sleep", lambda _seconds: None)

    with (
        cancellation_context(lambda: next(checks)),
        pytest.raises(RunCancelled),
    ):
        filesystem._run_in_isolated_runner("pytest -q", workspace)

    assert cancellations[-1].endswith("/jobs/job-1/cancel")


def test_isolated_runner_attempts_cleanup_after_network_loss(workspace, monkeypatch):
    class Response:
        ok = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    posts = []

    def post(url, **_kwargs):
        posts.append(url)
        return Response(
            {"job_id": "job-1", "status": "running"}
            if url.endswith("/jobs")
            else {"job_id": "job-1", "status": "cancelling"}
        )

    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(filesystem.requests, "post", post)
    monkeypatch.setattr(
        filesystem.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            filesystem.requests.ConnectionError("lost")
        ),
    )
    monkeypatch.setattr(filesystem.time, "sleep", lambda _seconds: None)

    result = filesystem._run_in_isolated_runner("pytest -q", workspace)

    assert result == {"error": "Isolated runner is unavailable."}
    assert posts[-1].endswith("/jobs/job-1/cancel")


# ============================================================
# Tier 2: scoped permissions enforcement in write/run tools
# ============================================================


def test_write_file_denied_under_read_scope(workspace):
    policy = PermissionPolicy(scope=READ)
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        result = filesystem.write_file.invoke(
            {"file_path": "note.txt", "content": "hello"}
        )

    assert "not permitted" in result["error"]


def test_edit_file_denied_under_read_scope(workspace):
    (workspace / "note.txt").write_text("before", encoding="utf-8")
    policy = PermissionPolicy(scope=READ)
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        result = filesystem.edit_file.invoke(
            {"file_path": "note.txt", "old_string": "before", "new_string": "after"}
        )

    assert "not permitted" in result["error"]
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "before"


def test_write_file_allowed_under_full_write_scope(workspace):
    policy = PermissionPolicy(scope=FULL_WRITE)
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        result = filesystem.write_file.invoke(
            {"file_path": "note.txt", "content": "hello"}
        )

    assert result["status"] == "written"


def test_write_file_denies_sensitive_paths_even_full_write(workspace):
    policy = PermissionPolicy(scope=FULL_WRITE)
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        result = filesystem.write_file.invoke(
            {"file_path": ".env", "content": "SECRET=1"}
        )

    assert "sensitive" in result["error"].lower()
    assert not (workspace / ".env").exists()


def test_write_file_scoped_to_allowed_roots(workspace):
    (workspace / "src").mkdir()
    policy = PermissionPolicy(scope=SCOPED_WRITE, edit_roots=(Path("src"),))
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        allowed = filesystem.write_file.invoke(
            {"file_path": "src/mod.py", "content": "x = 1"}
        )
        denied = filesystem.write_file.invoke(
            {"file_path": "README.md", "content": "# nope"}
        )

    assert allowed["status"] == "written"
    assert "edit scope" in denied["error"]


def test_write_file_does_not_clobber_same_basename_existing_file(workspace):
    """A write to a path that does not exist must CREATE that path, never be
    redirected onto a different existing file that shares its basename. This
    is the regression that destroyed server/app/agent/executor.py when a model
    wrote to the non-existent server/executor.py."""
    (workspace / "src").mkdir()
    existing = workspace / "src" / "deep" / "executor.py"
    existing.parent.mkdir(parents=True)
    existing.write_text("REAL IMPLEMENTATION\n", encoding="utf-8")
    policy = PermissionPolicy(scope=FULL_WRITE)
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        result = filesystem.write_file.invoke(
            {
                "file_path": "src/executor.py",
                "content": "stub",
                "overwrite": True,
            }
        )

    assert result["status"] == "written"
    assert result["path"] == "src/executor.py"
    assert (workspace / "src" / "executor.py").read_text(encoding="utf-8") == "stub"
    # The pre-existing same-basename file must be untouched.
    assert (workspace / "src" / "deep" / "executor.py").read_text(
        encoding="utf-8"
    ) == "REAL IMPLEMENTATION\n"


def test_edit_file_does_not_fall_back_to_same_basename_existing_file(workspace):
    """edit_file must fail with File not found when the requested path does
    not exist, instead of redirecting onto a same-basename file elsewhere."""
    (workspace / "src").mkdir()
    (workspace / "src" / "deep").mkdir()
    (workspace / "src" / "deep" / "executor.py").write_text(
        "def run(): pass\n", encoding="utf-8"
    )
    policy = PermissionPolicy(scope=FULL_WRITE)
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        result = filesystem.edit_file.invoke(
            {
                "file_path": "src/executor.py",
                "old_string": "def run(): pass",
                "new_string": "def run(): return 1",
            }
        )

    assert "File not found" in result["error"]
    assert (workspace / "src" / "deep" / "executor.py").read_text(
        encoding="utf-8"
    ) == "def run(): pass\n"


def test_edit_file_refuses_identical_old_and_new_text(workspace):
    target = workspace / "worker.py"
    target.write_text("def run(): pass\n", encoding="utf-8")

    with _full_write_ctx(workspace):
        result = filesystem.edit_file.invoke(
            {
                "file_path": "worker.py",
                "old_string": "def run(): pass",
                "new_string": "def run(): pass",
            }
        )

    assert "No-op edit refused" in result["error"]
    assert target.read_text(encoding="utf-8") == "def run(): pass\n"


def test_edit_file_supports_exact_edits_in_large_source_files(workspace):
    target = workspace / "large.py"
    target.write_text(
        "# padding\n" * 15_000 + "def target(): return 1\n", encoding="utf-8"
    )
    assert target.stat().st_size > filesystem.MAX_FILE_SIZE

    with _full_write_ctx(workspace):
        result = filesystem.edit_file.invoke(
            {
                "file_path": "large.py",
                "old_string": "def target(): return 1",
                "new_string": "def target(): return 2",
            }
        )

    assert result["status"] == "edited"
    assert target.read_text(encoding="utf-8").endswith("def target(): return 2\n")


def test_read_file_supports_targeted_line_range(workspace):
    target = workspace / "worker.py"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    with filesystem.workspace_context(str(workspace)):
        result = filesystem.read_file.invoke(
            {"file_path": "worker.py", "start_line": 2, "end_line": 3}
        )

    assert result == "two\nthree\n"


def test_read_file_streams_ranges_from_large_source_files(workspace):
    target = workspace / "large.py"
    target.write_text(
        "".join(f"line_{index}\n" for index in range(20_000)), encoding="utf-8"
    )
    assert target.stat().st_size > filesystem.MAX_FILE_SIZE

    with filesystem.workspace_context(str(workspace)):
        preview = filesystem.read_file.invoke({"file_path": "large.py"})
        targeted = filesystem.read_file.invoke(
            {"file_path": "large.py", "start_line": 19_999, "end_line": 20_000}
        )

    assert "large file preview" in preview
    assert "continue with start_line=" in preview
    assert targeted == "line_19998\nline_19999\n"


def test_run_command_denied_by_request_allowlist(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    monkeypatch.setattr(filesystem, "ALLOWED_COMMANDS", ["echo", "git"])
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda command, cwd, tier="isolated": {
            "command": command,
            "exit_code": 0,
            "output": "ok",
        },
    )
    policy = PermissionPolicy(command_allowlist=frozenset({"echo"}))
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        allowed = filesystem.run_command.invoke({"command": "echo hi"})
        denied = filesystem.run_command.invoke({"command": "git status"})

    assert allowed["exit_code"] == 0
    assert "command allowlist" in denied["error"]


def test_run_tests_does_not_hit_runner_under_read_scope(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    calls = []
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda command, cwd, tier="isolated": calls.append(tier) or {},
    )
    policy = PermissionPolicy(command_allowlist=frozenset({"pytest"}))
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke({"kind": "pytest"})

    assert result["kind"] == "pytest"
    assert calls == ["isolated"]


def test_run_tests_supports_focused_pytest_node(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    test_file = workspace / "tests" / "test_worker.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_retry(): pass\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda command, cwd, tier="isolated": calls.append((command, cwd))
        or {"exit_code": 0, "output": "1 passed"},
    )
    with filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke(
            {
                "kind": "pytest",
                "test_path": "tests/test_worker.py::test_retry",
            }
        )

    assert result["exit_code"] == 0
    assert calls == [
        ("pytest -q tests/test_worker.py::test_retry", workspace),
    ]


def test_run_tests_supports_focused_ruff_file(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "SANDBOX_ROOT", workspace.parent)
    source_file = workspace / "app" / "worker.py"
    source_file.parent.mkdir()
    source_file.write_text("def retry(): pass\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda command, cwd, tier="isolated": calls.append((command, cwd))
        or {"exit_code": 0, "output": "All checks passed!"},
    )
    with filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke(
            {"kind": "ruff", "test_path": "app/worker.py"}
        )

    assert result["exit_code"] == 0
    assert calls == [("ruff check app/worker.py", workspace)]


def test_run_tests_rejects_unsafe_focused_target(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda *args, **kwargs: calls.append(args) or {},
    )
    with filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke(
            {"kind": "pytest", "test_path": "tests/test_worker.py;uname"}
        )

    assert "unsupported characters" in result["error"]
    assert calls == []


def test_run_tests_rejects_application_source_as_pytest_target(workspace, monkeypatch):
    source_file = workspace / "app" / "worker.py"
    source_file.parent.mkdir()
    source_file.write_text("def retry(): pass\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        filesystem,
        "_run_in_isolated_runner",
        lambda *args, **kwargs: calls.append(args) or {},
    )
    with filesystem.workspace_context(str(workspace)):
        result = filesystem.run_tests.invoke(
            {"kind": "pytest", "test_path": "app/worker.py"}
        )

    assert "must name a test module" in result["error"]
    assert calls == []


@contextmanager
def _full_write_ctx(workspace):
    with (
        permissions_context(PermissionPolicy(scope=FULL_WRITE)),
        filesystem.workspace_context(str(workspace)),
    ):
        yield


def test_apply_patch_exact_match(workspace):
    (workspace / "a.txt").write_text("line one\nline two\n", encoding="utf-8")
    with _full_write_ctx(workspace):
        result = filesystem.apply_patch.invoke(
            {"file_path": "a.txt", "old_string": "line two", "new_string": "changed"}
        )

    assert result["status"] == "applied"
    assert result["match"] == "exact"
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "line one\nchanged\n"


def test_apply_patch_fuzzy_whitespace_drift(workspace):
    (workspace / "a.txt").write_text("def f():\n    print('hello')\n", encoding="utf-8")
    with _full_write_ctx(workspace):
        # Model guesses a similar-but-not-identical line.
        result = filesystem.apply_patch.invoke(
            {
                "file_path": "a.txt",
                "old_string": "print('hell')",
                "new_string": "print('bye')",
            }
        )

    assert result["status"] == "applied"
    assert result["match"] == "fuzzy"
    assert result["confidence"] >= 0.8
    assert (workspace / "a.txt").read_text(encoding="utf-8") == (
        "def f():\n    print('bye')\n"
    )


def test_apply_patch_refuses_ambiguous_fuzzy_match(workspace):
    (workspace / "a.txt").write_text(
        "AAA\nfoo bar baz\nBBB\nfoo bar bax\nCCC\n", encoding="utf-8"
    )
    with _full_write_ctx(workspace):
        result = filesystem.apply_patch.invoke(
            {
                "file_path": "a.txt",
                "old_string": "foo bar bay",
                "new_string": "X",
            }
        )

    assert result["error"]
    assert (workspace / "a.txt").read_text(encoding="utf-8") == (
        "AAA\nfoo bar baz\nBBB\nfoo bar bax\nCCC\n"
    )


def test_apply_patch_rejects_unrelated_text(workspace):
    (workspace / "a.txt").write_text("hello world\n", encoding="utf-8")
    with _full_write_ctx(workspace):
        result = filesystem.apply_patch.invoke(
            {"file_path": "a.txt", "old_string": "totally unrelated", "new_string": "X"}
        )

    assert "no close match" in result["error"]
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hello world\n"


def test_apply_patch_refuses_to_rename_def_identifier(workspace):
    """A fuzzy match must never rename a def/class: matching cancel_job onto
    a similarly worded _watch_job is a corruption, not an edit."""
    (workspace / "a.txt").write_text(
        "def _watch_job(job):\n    return job\n", encoding="utf-8"
    )
    with _full_write_ctx(workspace):
        result = filesystem.apply_patch.invoke(
            {
                "file_path": "a.txt",
                "old_string": "def cancel_job(job_id: str) -> None:",
                "new_string": '    """Cancels a running job."""\n    def cancel_job(job_id: str) -> None:',
            }
        )

    assert result["error"]
    assert "no close match" in result["error"] or "not found" in result["error"]
    # The _watch_job function must be untouched.
    assert "def _watch_job(job):" in (workspace / "a.txt").read_text(encoding="utf-8")


def test_apply_patch_fuzzy_match_keeps_same_identifier(workspace):
    """Signature drift on the same def identifier may still fuzzy-match."""
    (workspace / "a.txt").write_text(
        "def cancel_job(job_id):\n    return job_id\n", encoding="utf-8"
    )
    with _full_write_ctx(workspace):
        result = filesystem.apply_patch.invoke(
            {
                "file_path": "a.txt",
                "old_string": "def cancel_job(job_id: str) -> None:",
                "new_string": 'def cancel_job(job_id):\n    """Cancels."""\n    return job_id',
            }
        )

    assert result["status"] == "applied"
    assert result["match"] == "fuzzy"
    text = (workspace / "a.txt").read_text(encoding="utf-8")
    assert "def cancel_job(job_id):" in text
    assert "Cancels." in text


def test_apply_patch_denied_under_read_scope(workspace):
    (workspace / "a.txt").write_text("before\n", encoding="utf-8")
    policy = PermissionPolicy(scope=READ)
    with permissions_context(policy), filesystem.workspace_context(str(workspace)):
        result = filesystem.apply_patch.invoke(
            {"file_path": "a.txt", "old_string": "before", "new_string": "after"}
        )

    assert "not permitted" in result["error"]
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "before\n"


def test_apply_patch_dry_run_does_not_modify(workspace):
    (workspace / "a.txt").write_text("keep me\n", encoding="utf-8")
    with _full_write_ctx(workspace):
        result = filesystem.apply_patch.invoke(
            {
                "file_path": "a.txt",
                "old_string": "keep me",
                "new_string": "gone",
                "dry_run": True,
            }
        )

    assert result["status"] == "dry_run"
    assert "+gone" in result["diff"]
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "keep me\n"
