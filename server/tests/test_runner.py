import threading

import pytest
from fastapi import HTTPException

from app import runner


def test_runner_surfaces_kill_failure_without_waiting_forever(monkeypatch):
    class Process:
        pid = 123
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            assert timeout is not None
            raise runner.subprocess.TimeoutExpired(["command"], timeout)

        def communicate(self, timeout=None):
            assert timeout is not None
            raise runner.subprocess.TimeoutExpired(["command"], timeout)

    job = runner.RunnerJob(
        id="job-1",
        command="command",
        directory="/sandbox",
        tier="isolated",
        process=Process(),
        lease_deadline=0,
    )
    monkeypatch.setattr(runner, "_terminate_process_group", lambda _job: False)

    reader = threading.Thread(target=lambda: None)
    runner._watch_job(job, reader)

    assert job.status == "kill_failed"
    assert "survived SIGKILL" in job.output


def test_runner_rejects_unknown_sandbox_tier(configured_runner):
    with pytest.raises(HTTPException) as error:
        runner.create_job(
            runner.ExecuteRequest(
                command='python3 -c "print(42)"',
                directory=str(configured_runner),
                tier="not-a-tier",
            ),
            x_runner_key="secret",
        )
