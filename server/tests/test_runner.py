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
    # Job lifecycle tests do not exercise namespace creation. Dedicated tests
    # below cover the fail-closed isolation wrapper.
    monkeypatch.setattr(runner, "_command_argv", lambda parts, _tier: parts)
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
        tier="isolated",
        process=Process(),
        lease_deadline=0,
    )
    monkeypatch.setattr(runner, "_terminate_process_group", lambda _job: False)

    runner._watch_job(job)

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

    assert error.value.status_code == 400
    assert "Unsupported sandbox tier" in error.value.detail


def test_rlimit_args_uses_tier_limits():
    limits = runner._rlimit_args("isolated")
    assert limits["cpu_seconds"] == runner.RUNNER_CPU_SECONDS
    assert limits["memory_mb"] == runner.RUNNER_MEMORY_MB
    assert limits["max_open_files"] == runner.RUNNER_MAX_OPEN_FILES


def test_command_argv_wraps_isolated_tier_in_netns_when_available(monkeypatch):
    monkeypatch.setattr(runner, "RUNNER_ENABLE_NETNS", True)
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/unshare")
    monkeypatch.setattr(runner, "_netns_available", lambda: True)

    argv = runner._command_argv(["python3", "-c", "x"], "isolated")
    assert argv == [
        "/usr/bin/unshare",
        "--map-root-user",
        "--net",
        "python3",
        "-c",
        "x",
    ]


def test_command_argv_fails_closed_when_netns_is_unavailable(monkeypatch):
    monkeypatch.setattr(runner, "RUNNER_ENABLE_NETNS", True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "/usr/bin/unshare")
    monkeypatch.setattr(runner, "_netns_available", lambda: False)

    with pytest.raises(HTTPException) as error:
        runner._command_argv(["python3", "-c", "x"], "isolated")

    assert error.value.status_code == 503
    assert "private network namespace" in error.value.detail


def test_command_argv_network_tier_is_not_wrapped(monkeypatch):
    monkeypatch.setattr(runner, "RUNNER_ENABLE_NETNS", True)
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/unshare")
    monkeypatch.setattr(runner, "_netns_available", lambda: True)

    assert runner._command_argv(["python3", "-c", "x"], "network") == [
        "python3",
        "-c",
        "x",
    ]


def test_command_argv_fails_closed_when_netns_is_disabled(monkeypatch):
    monkeypatch.setattr(runner, "RUNNER_ENABLE_NETNS", False)

    with pytest.raises(HTTPException) as error:
        runner._command_argv(["python3", "-c", "x"], "isolated")

    assert error.value.status_code == 503
    assert "enforcement is disabled" in error.value.detail


def test_runner_applies_tier_resource_limits_in_child(configured_runner):
    probe = (
        "import resource\n"
        "print(resource.getrlimit(resource.RLIMIT_CPU)[1])\n"
        "print(resource.getrlimit(resource.RLIMIT_NOFILE)[1])\n"
        "print(resource.getrlimit(resource.RLIMIT_AS)[1])\n"
    )
    payload = runner.create_job(
        runner.ExecuteRequest(
            command=f'python3 -c "{probe}"',
            directory=str(configured_runner),
        ),
        x_runner_key="secret",
    )

    completed = _wait_for_status(payload["job_id"], {"completed"})
    cpu, nofile, address_space = [
        int(line) for line in completed["output"].splitlines() if line
    ]

    assert cpu == runner.RUNNER_CPU_SECONDS
    assert nofile == runner.RUNNER_MAX_OPEN_FILES
    assert address_space == runner.RUNNER_MEMORY_MB * 1024 * 1024
