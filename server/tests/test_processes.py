import os
import subprocess
import time
from pathlib import Path

import pytest

from app.core import processes
from app.core.cancellation import cancellation_context
from app.core.exceptions import ProcessKillFailed, RunCancelled
from app.core.processes import run_cancellable


def test_cancellation_stops_complete_child_process_group(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess,time;"
        "child=subprocess.Popen(['sleep','30']);"
        f"open({str(child_pid_file)!r},'w').write(str(child.pid));"
        "time.sleep(30)"
    )
    started = time.monotonic()

    with (
        cancellation_context(child_pid_file.exists),
        pytest.raises(RunCancelled),
    ):
        run_cancellable(
            ["python3", "-c", script],
            timeout=30,
        )

    assert time.monotonic() - started < 3
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            state = (Path("/proc") / str(child_pid) / "stat").read_text().split()[2]
        except OSError:
            break
        if state == "Z":
            break
        time.sleep(0.02)
    else:
        os.kill(child_pid, 0)
        pytest.fail("child process remained active after group cancellation")


def test_process_timeout_terminates_command():
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        run_cancellable(
            ["python3", "-c", "__import__('time').sleep(30)"],
            timeout=0.05,
        )

    assert time.monotonic() - started < 3


def test_failed_sigkill_is_reported_without_blocking_communicate(monkeypatch):
    class Process:
        pid = 123
        returncode = None
        communicate_calls = 0

        def communicate(self, **_kwargs):
            self.communicate_calls += 1
            raise AssertionError("must not wait after SIGKILL failed")

    process = Process()
    monkeypatch.setattr(
        processes.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(processes, "terminate_process_group", lambda _process: False)

    with (
        cancellation_context(lambda: True),
        pytest.raises(ProcessKillFailed, match="SIGKILL"),
    ):
        run_cancellable(["command"], timeout=30)

    assert process.communicate_calls == 0
