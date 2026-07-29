"""Private, cancellable command runner for disposable agent worktrees."""

from __future__ import annotations

import hmac
import os
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

SANDBOX_ROOT = Path(os.getenv("SANDBOX_ROOT", "/sandboxes")).resolve()
RUNNER_API_KEY = os.getenv("RUNNER_API_KEY", "")
COMMAND_TIMEOUT_SECONDS = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "120"))
TERMINATE_GRACE_SECONDS = float(os.getenv("RUNNER_TERMINATE_GRACE_SECONDS", "3"))
JOB_LEASE_SECONDS = float(os.getenv("RUNNER_JOB_LEASE_SECONDS", "10"))
JOB_RETENTION_SECONDS = int(os.getenv("RUNNER_JOB_RETENTION_SECONDS", "300"))
MAX_RETAINED_JOBS = int(os.getenv("RUNNER_MAX_RETAINED_JOBS", "1000"))
MAX_OUTPUT_CHARS = int(os.getenv("RUNNER_MAX_OUTPUT_CHARS", "30000"))
ALLOWED = {
    item.strip()
    for item in os.getenv(
        "ALLOWED_COMMANDS",
        "git,pytest,python,python3,npm,node,make,mypy,ruff,black,flake8",
    ).split(",")
    if item.strip()
}
TERMINAL_STATUSES = {"completed", "cancelled", "timed_out", "kill_failed"}

app = FastAPI(title="Private agent runner", version="1.0")


class ExecuteRequest(BaseModel):
    command: str
    directory: str


@dataclass
class RunnerJob:
    id: str
    command: str
    directory: str
    process: subprocess.Popen[str]
    status: str = "running"
    output: str = ""
    exit_code: int | None = None
    cancel_requested: bool = False
    cancel_reason: str | None = None
    created_at: float = field(default_factory=time.time)
    lease_deadline: float = field(
        default_factory=lambda: time.monotonic() + JOB_LEASE_SECONDS
    )
    completed_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_jobs: dict[str, RunnerJob] = {}
_jobs_lock = threading.Lock()


def _authorize(x_runner_key: str | None) -> None:
    if (
        not RUNNER_API_KEY
        or not x_runner_key
        or not hmac.compare_digest(x_runner_key, RUNNER_API_KEY)
    ):
        raise HTTPException(401, "Invalid runner key")


def _validated_command(request: ExecuteRequest) -> tuple[list[str], Path]:
    forbidden = ["&&", "||", "|", ";", ">", "<", "`", "$("]
    if any(token in request.command for token in forbidden):
        raise HTTPException(400, "Command chaining/redirection is not permitted")
    try:
        parts = shlex.split(request.command)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid command: {exc}") from exc
    if not parts or parts[0] not in ALLOWED:
        raise HTTPException(400, "Command is not approved")
    directory = Path(request.directory).resolve()
    if directory == SANDBOX_ROOT or SANDBOX_ROOT not in directory.parents:
        raise HTTPException(403, "Runner accepts only sandbox worktrees")
    if not directory.is_dir():
        raise HTTPException(400, "Sandbox directory does not exist")
    return parts, directory


def _trim_output(stdout: str | None, stderr: str | None) -> str:
    output = ((stdout or "") + "\n" + (stderr or "")).strip()
    return output[-MAX_OUTPUT_CHARS:]


def _terminate_process_group(job: RunnerJob) -> bool:
    """Terminate a complete command tree, escalating to SIGKILL."""

    process = job.process
    if process.poll() is not None:
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
        return True
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        try:
            process.wait(timeout=TERMINATE_GRACE_SECONDS)
            return True
        except subprocess.TimeoutExpired:
            return False


def _watch_job(job: RunnerJob) -> None:
    timed_out = False
    killed = True
    command_deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    while True:
        try:
            stdout, stderr = job.process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            with job.lock:
                owner_expired = now >= job.lease_deadline
                if owner_expired and not job.cancel_requested:
                    job.cancel_requested = True
                    job.cancel_reason = "owner_lease_expired"
                    job.status = "cancelling"
            if owner_expired:
                killed = _terminate_process_group(job)
                if killed:
                    stdout, stderr = job.process.communicate()
                else:
                    stdout, stderr = "", "Process group survived SIGKILL"
                break
            if now >= command_deadline:
                timed_out = True
                killed = _terminate_process_group(job)
                if killed:
                    stdout, stderr = job.process.communicate()
                else:
                    stdout, stderr = "", "Process group survived SIGKILL"
                break
    with job.lock:
        job.output = _trim_output(stdout, stderr)
        job.exit_code = job.process.returncode
        if not killed:
            job.status = "kill_failed"
        elif job.cancel_requested:
            job.status = "cancelled"
        elif timed_out:
            job.status = "timed_out"
            job.exit_code = 124
            if not job.output:
                job.output = "Command timed out"
        else:
            job.status = "completed"
        job.completed_at = time.time()


def _cleanup_jobs() -> None:
    cutoff = time.time() - JOB_RETENTION_SECONDS
    with _jobs_lock:
        expired = [
            job_id
            for job_id, job in _jobs.items()
            if job.completed_at is not None and job.completed_at < cutoff
        ]
        for job_id in expired:
            _jobs.pop(job_id, None)
        overflow = max(0, len(_jobs) - MAX_RETAINED_JOBS)
        completed = sorted(
            (job for job in _jobs.values() if job.completed_at is not None),
            key=lambda job: job.completed_at or 0,
        )
        for job in completed[:overflow]:
            _jobs.pop(job.id, None)


def _job_payload(job: RunnerJob, *, renew_lease: bool = False) -> dict[str, Any]:
    with job.lock:
        if renew_lease and job.status not in TERMINAL_STATUSES:
            job.lease_deadline = time.monotonic() + JOB_LEASE_SECONDS
        return {
            "job_id": job.id,
            "status": job.status,
            "exit_code": job.exit_code,
            "output": job.output,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "cancellation_reason": job.cancel_reason,
        }


def _get_job(job_id: str) -> RunnerJob:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Runner job not found or already expired")
    return job


def _start_job(request: ExecuteRequest) -> RunnerJob:
    parts, directory = _validated_command(request)
    _cleanup_jobs()
    try:
        process = subprocess.Popen(
            parts,
            cwd=directory,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": "/tmp/runner",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            # Safe in a threaded ASGI process and gives cancellation a process
            # group containing every child spawned by the command.
            start_new_session=True,
        )
    except OSError as exc:
        raise HTTPException(500, f"Could not start command: {exc}") from exc
    job = RunnerJob(
        id=str(uuid.uuid4()),
        command=request.command,
        directory=str(directory),
        process=process,
    )
    with _jobs_lock:
        _jobs[job.id] = job
    threading.Thread(
        target=_watch_job,
        args=(job,),
        daemon=True,
        name=f"runner-job-{job.id[:8]}",
    ).start()
    return job


@app.post("/jobs")
def create_job(request: ExecuteRequest, x_runner_key: str | None = Header(None)):
    _authorize(x_runner_key)
    return _job_payload(_start_job(request))


@app.get("/jobs/{job_id}")
def get_job(job_id: str, x_runner_key: str | None = Header(None)):
    _authorize(x_runner_key)
    return _job_payload(_get_job(job_id), renew_lease=True)


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, x_runner_key: str | None = Header(None)):
    _authorize(x_runner_key)
    job = _get_job(job_id)
    with job.lock:
        terminal = job.status in TERMINAL_STATUSES
        if not terminal:
            job.cancel_requested = True
            job.cancel_reason = "requested"
            job.status = "cancelling"
    if terminal:
        return _job_payload(job)
    if not _terminate_process_group(job):
        with job.lock:
            job.status = "kill_failed"
            job.completed_at = time.time()
    return _job_payload(job)


@app.post("/execute")
def execute(request: ExecuteRequest, x_runner_key: str | None = Header(None)):
    """Backward-compatible blocking endpoint for older agent replicas."""

    _authorize(x_runner_key)
    job = _start_job(request)
    while True:
        payload = _job_payload(job, renew_lease=True)
        if payload["status"] in TERMINAL_STATUSES:
            return payload
        time.sleep(0.05)
