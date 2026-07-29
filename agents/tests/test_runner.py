import time

import pytest
from fastapi import HTTPException

from app import runner


def _wait_for_status(
    job_id: str,
    statuses: set[str],
    timeout: float = 5,
    *,
    renew_lease: bool = True,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = (
            runner.get_job(job_id, x_runner_key="secret")
            if renew_lease
            else runner._job_payload(runner._get_job(job_id))
        )
        if payload["status"] in statuses:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"runner job {job_id} did not reach {statuses}")


@pytest.fixture
def configured_runner(tmp_path, monkeypatch):
    sandbox = tmp_path / "sandboxes"
    worktree = sandbox / "run-1"
    worktree.mkdir(parents=True)
    monkeypatch.setattr(runner, "SANDBOX_ROOT", sandbox)
    monkeypatch.setattr(runner, "RUNNER_API_KEY", "secret")
    monkeypatch.setattr(runner, "ALLOWED", {"python3"})
    monkeypatch.setattr(runner, "TERMINATE_GRACE_SECONDS", 0.2)
    runner._jobs.clear()
    yield worktree
    for job in list(runner._jobs.values()):
        runner._terminate_process_group(job)
    runner._jobs.clear()


def test_runner_jobs_have_stable_ids_and_complete(configured_runner):
    payload = runner.create_job(
        runner.ExecuteRequest(
            command='python3 -c "print(42)"',
            directory=str(configured_runner),
        ),
        x_runner_key="secret",
    )

    completed = _wait_for_status(payload["job_id"], {"completed"})

    assert completed["job_id"] == payload["job_id"]
    assert completed["exit_code"] == 0
    assert completed["output"] == "42"


def test_runner_cancel_terminates_active_process_group(configured_runner):
    payload = runner.create_job(
        runner.ExecuteRequest(
            command="python3 -c \"__import__('time').sleep(30)\"",
            directory=str(configured_runner),
        ),
        x_runner_key="secret",
    )

    cancelling = runner.cancel_job(payload["job_id"], x_runner_key="secret")
    completed = _wait_for_status(
        payload["job_id"],
        {"cancelled", "kill_failed"},
        renew_lease=False,
    )

    assert cancelling["status"] in {"cancelling", "cancelled"}
    assert completed["status"] == "cancelled"
    assert runner._get_job(payload["job_id"]).process.poll() is not None


def test_runner_timeout_is_distinct_from_command_failure(
    configured_runner,
    monkeypatch,
):
    monkeypatch.setattr(runner, "COMMAND_TIMEOUT_SECONDS", 0.05)
    payload = runner.create_job(
        runner.ExecuteRequest(
            command="python3 -c \"__import__('time').sleep(30)\"",
            directory=str(configured_runner),
        ),
        x_runner_key="secret",
    )

    completed = _wait_for_status(payload["job_id"], {"timed_out", "kill_failed"})

    assert completed["status"] == "timed_out"
    assert completed["exit_code"] == 124


def test_runner_stops_job_when_api_owner_disappears(
    configured_runner,
    monkeypatch,
):
    monkeypatch.setattr(runner, "JOB_LEASE_SECONDS", 0.05)
    payload = runner.create_job(
        runner.ExecuteRequest(
            command="python3 -c \"__import__('time').sleep(30)\"",
            directory=str(configured_runner),
        ),
        x_runner_key="secret",
    )

    completed = _wait_for_status(
        payload["job_id"],
        {"cancelled", "kill_failed"},
        renew_lease=False,
    )

    assert completed["status"] == "cancelled"
    assert completed["cancellation_reason"] == "owner_lease_expired"


def test_runner_rejects_missing_authentication(configured_runner):
    with pytest.raises(HTTPException) as error:
        runner.create_job(
            runner.ExecuteRequest(
                command='python3 -c "print(42)"',
                directory=str(configured_runner),
            ),
            x_runner_key=None,
        )

    assert error.value.status_code == 401


def test_runner_surfaces_kill_failure_without_waiting_forever(monkeypatch):
    class Process:
        pid = 123
        returncode = None

        def communicate(self, timeout=None):
            assert timeout is not None
            raise runner.subprocess.TimeoutExpired(["command"], timeout)

    job = runner.RunnerJob(
        id="job-1",
        command="command",
        directory="/sandbox",
        process=Process(),
        lease_deadline=0,
    )
    monkeypatch.setattr(runner, "_terminate_process_group", lambda _job: False)

    runner._watch_job(job)

    assert job.status == "kill_failed"
    assert "survived SIGKILL" in job.output
