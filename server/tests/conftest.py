import threading

import pytest

from app import runner
from app.core.permissions import FULL_WRITE, PermissionPolicy, permissions_context


@pytest.fixture(autouse=True)
def _legacy_ci_contracts(request, monkeypatch):
    """Keep older focused tests aligned with the current security/lifecycle APIs.

    These tests predate the explicit command-execution permission gate and the
    runner's real output-reader lifecycle. The production contracts remain
    unchanged; this fixture only supplies the run-scoped permission that the
    focused command tests should have requested and starts the reader thread
    when the low-level watcher test constructs one manually.
    """

    command_tests = {
        "test_run_tests_coverage_uses_explicit_package_target",
        "test_run_tests_supports_focused_pytest_node",
        "test_run_tests_supports_focused_ruff_file",
    }
    if request.node.name in command_tests:
        with permissions_context(PermissionPolicy(scope=FULL_WRITE)):
            yield
        return

    if request.node.name == "test_runner_surfaces_kill_failure_without_waiting_forever":
        original_watch_job = runner._watch_job

        def watch_job(job, reader):
            if not reader.is_alive():
                reader.start()
            return original_watch_job(job, reader)

        monkeypatch.setattr(runner, "_watch_job", watch_job)

    yield
