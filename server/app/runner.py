"""Private, cancellable command runner for disposable agent worktrees."""

from __future__ import annotations

import hmac
import os
import shlex
import shutil
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

# Sandbox tiers: per-tier network access and kernel resource limits applied to
# every command. The "isolated" tier is the default for review worktrees and
# requires a private network namespace. It fails closed when that boundary
# cannot be created. The explicit "network" tier keeps CPU/memory/open-file
# limits but allows the command to reach the runner network.
RUNNER_CPU_SECONDS = int(os.getenv("RUNNER_CPU_SECONDS", "90"))
RUNNER_MEMORY_MB = int(os.getenv("RUNNER_MEMORY_MB", "2048"))
RUNNER_MAX_OPEN_FILES = int(os.getenv("RUNNER_MAX_OPEN_FILES", "256"))
RUNNER_DOCKER_IMAGE = os.getenv("RUNNER_DOCKER_IMAGE", "").strip()
RUNNER_NETWORK_TIERS = {"network", "docker"}
RUNNER_ENABLE_NETNS = os.getenv("RUNNER_ENABLE_NETNS", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

TIERS: dict[str, dict[str, Any]] = {
    "isolated": {
        "network": False,
        "cpu_seconds": RUNNER_CPU_SECONDS,
        "memory_mb": RUNNER_MEMORY_MB,
        "max_open_files": RUNNER_MAX_OPEN_FILES,
    },
    "network": {
        "network": True,
        "cpu_seconds": RUNNER_CPU_SECONDS,
        "memory_mb": RUNNER_MEMORY_MB,
        "max_open_files": RUNNER_MAX_OPEN_FILES,
    },
    "docker": {
        "network": False,
        "cpu_seconds": RUNNER_CPU_SECONDS,
        "memory_mb": RUNNER_MEMORY_MB,
        "max_open_files": RUNNER_MAX_OPEN_FILES,
        "docker_image": RUNNER_DOCKER_IMAGE,
    },
}

app = FastAPI(title="Private agent runner", version="1.0")


class ExecuteRequest(BaseModel):
    command: str
    directory: str
    tier: str = "isolated"


class InputRequest(BaseModel):
    data: str


@dataclass
class RunnerJob:
    id: str
    command: str
    directory: str
    tier: str
    process: subprocess.Popen[str]
    status: str = "running"
    output: str = ""
    output_base: int = 0
    output_total: int = 0
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
    if request.tier not in TIERS:
        raise HTTPException(
            400,
            f"Unsupported sandbox tier: {request.tier}. "
            f"Supported tiers: {', '.join(sorted(TIERS))}",
        )
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


def _rlimit_args(tier: str) -> dict[str, Any]:
    """Kernel resource limits (RLIMIT_CPU, RLIMIT_AS, RLIMIT_NOFILE) for a tier."""

    limits = TIERS.get(tier, TIERS["isolated"])
    return {
        "cpu_seconds": limits["cpu_seconds"],
        "memory_mb": limits["memory_mb"],
        "max_open_files": limits["max_open_files"],
    }


def _apply_limits(limits: dict[str, Any]) -> None:
    """Set resource limits in the child before exec.

    Runs from subprocess.Popen's preexec_fn, so it must only call
    async-signal-safe syscalls: resource.setrlimit is a raw syscall and is
    safe to use even though preexec_fn in a threaded process is otherwise
    discouraged.
    """

    import resource

    cpu = int(limits.get("cpu_seconds") or 0)
    memory = int(limits.get("memory_mb") or 0)
    nofile = int(limits.get("max_open_files") or 0)
    if cpu > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    if nofile > 0:
        resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
    if memory > 0:
        resource.setrlimit(
            resource.RLIMIT_AS, (memory * 1024 * 1024, memory * 1024 * 1024)
        )


_netns_checked = False
_netns_works = False


def _netns_available() -> bool:
    """Whether ``unshare -n`` (network namespace) is usable on this host."""

    global _netns_checked, _netns_works
    if not _netns_checked:
        _netns_checked = True
        unshare = shutil.which("unshare")
        if unshare:
            try:
                probe = subprocess.run(
                    [unshare, "--map-root-user", "--net", "true"],
                    timeout=10,
                    capture_output=True,
                )
                _netns_works = probe.returncode == 0
            except (OSError, subprocess.SubprocessError):
                _netns_works = False
    return _netns_works


def _command_argv(parts: list[str], tier: str) -> list[str]:
    """Wrap the command for its tier.

    The "isolated" tier drops network access by running the command in a
    private user and network namespace. Isolation is a security contract:
    unavailable or disabled namespace support rejects the job instead of
    silently executing it with weaker boundaries.
    """

    limits = TIERS.get(tier, TIERS["isolated"])
    if limits["network"]:
        return parts
    if not RUNNER_ENABLE_NETNS:
        raise HTTPException(
            503,
            "The isolated runner tier is unavailable because network namespace "
            "enforcement is disabled. Use the explicit network tier only when "
            "network access is approved.",
        )
    unshare = shutil.which("unshare")
    if unshare and _netns_available():
        return [unshare, "--map-root-user", "--net", *parts]
    raise HTTPException(
        503,
        "The isolated runner tier cannot create a private network namespace. "
        "Enable unprivileged user namespaces or install a supported sandbox.",
    )


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


def _append_output(job: RunnerJob, chunk: str) -> None:
    if not chunk:
        return
    with job.lock:
        combined = job.output + chunk
        job.output_total += len(chunk)
        if len(combined) > MAX_OUTPUT_CHARS:
            dropped = len(combined) - MAX_OUTPUT_CHARS
            job.output_base += dropped
            combined = combined[dropped:]
        job.output = combined


def _stream_output(job: RunnerJob) -> None:
    stream = job.process.stdout
    if stream is None:
        return
    try:
        for chunk in iter(stream.readline, ""):
            _append_output(job, chunk)
    finally:
        stream.close()


def _watch_job(job: RunnerJob, reader: threading.Thread) -> None:
    timed_out = False
    killed = True
    command_deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    while job.process.poll() is None:
        now = time.monotonic()
        with job.lock:
            owner_expired = now >= job.lease_deadline
            if owner_expired and not job.cancel_requested:
                job.cancel_requested = True
                job.cancel_reason = "owner_lease_expired"
                job.status = "cancelling"
        if owner_expired:
            killed = _terminate_process_group(job)
            break
        if now >= command_deadline:
            timed_out = True
            killed = _terminate_process_group(job)
            break
        time.sleep(0.05)

    try:
        job.process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        killed = False
    reader.join(timeout=TERMINATE_GRACE_SECONDS)
    with job.lock:
        job.exit_code = job.process.returncode
        if not killed:
            job.status = "kill_failed"
            if not job.output:
                job.output = "Command survived SIGKILL and could not be terminated"
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
            "tier": job.tier,
            "exit_code": job.exit_code,
            "output": job.output.strip(),
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
    job_id = str(uuid.uuid4())
    argv = _command_argv(parts, request.tier)
    limits = _rlimit_args(request.tier)
    try:
        process = subprocess.Popen(
            argv,
            cwd=directory,
            text=True,
            bufsize=1,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": "/tmp/runner",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                # pytest-cov otherwise writes a binary .coverage file into the
                # review worktree and pollutes an otherwise focused code diff.
                "COVERAGE_FILE": f"/tmp/aistack-coverage-{job_id}",
            },
            # Safe in a threaded ASGI process and gives cancellation a process
            # group containing every child spawned by the command.
            start_new_session=True,
            # Bounded by the tier's kernel limits before the command execs.
            preexec_fn=lambda: _apply_limits(limits),
        )
    except OSError as exc:
        raise HTTPException(500, f"Could not start command: {exc}") from exc
    job = RunnerJob(
        id=job_id,
        command=request.command,
        directory=str(directory),
        tier=request.tier,
        process=process,
    )
    with _jobs_lock:
        _jobs[job.id] = job
    reader = threading.Thread(
        target=_stream_output,
        args=(job,),
        daemon=True,
        name=f"runner-output-{job.id[:8]}",
    )
    reader.start()
    threading.Thread(
        target=_watch_job,
        args=(job, reader),
        daemon=True,
        name=f"runner-job-{job.id[:8]}",
    ).start()
    return job


def isolation_status() -> dict[str, Any]:
    """Return deploy-time isolation capability without weakening fail-closed jobs."""

    enabled = RUNNER_ENABLE_NETNS
    unshare = shutil.which("unshare")
    available = bool(enabled and unshare and _netns_available())
    reason = None
    if not enabled:
        reason = "RUNNER_ENABLE_NETNS is disabled"
    elif not unshare:
        reason = "unshare is not installed"
    elif not available:
        reason = "private user/network namespace probe failed"
    return {
        "ready": available,
        "backend": "user-network-namespace",
        "enabled": enabled,
        "unshare": unshare,
        "reason": reason,
    }


@app.get("/health")
def health():
    return {"status": "alive"}


@app.get("/ready")
def ready():
    """Report API readiness separately from optional isolated-tier capability."""

    if not RUNNER_API_KEY:
        raise HTTPException(503, "RUNNER_API_KEY is required")
    return {
        "ready": True,
        "isolation": isolation_status(),
    }


@app.post("/jobs")
def create_job(request: ExecuteRequest, x_runner_key: str | None = Header(None)):
    _authorize(x_runner_key)
    return _job_payload(_start_job(request))


@app.get("/jobs/{job_id}")
def get_job(job_id: str, x_runner_key: str | None = Header(None)):
    _authorize(x_runner_key)
    return _job_payload(_get_job(job_id), renew_lease=True)


@app.get("/jobs/{job_id}/output")
def job_output(
    job_id: str,
    after: int = 0,
    x_runner_key: str | None = Header(None),
):
    """Return output added after a caller-owned character cursor."""

    _authorize(x_runner_key)
    job = _get_job(job_id)
    with job.lock:
        requested = max(0, int(after))
        absolute = max(requested, job.output_base)
        relative = min(len(job.output), absolute - job.output_base)
        return {
            "job_id": job.id,
            "status": job.status,
            "output": job.output[relative:],
            "next_offset": job.output_total,
            "truncated": requested < job.output_base,
        }


@app.post("/jobs/{job_id}/input")
def send_job_input(
    job_id: str,
    request: InputRequest,
    x_runner_key: str | None = Header(None),
):
    """Steer an active command through its standard input."""

    _authorize(x_runner_key)
    if len(request.data) > 16_384:
        raise HTTPException(413, "Runner input exceeds 16384 characters")
    job = _get_job(job_id)
    with job.lock:
        if job.status in TERMINAL_STATUSES or job.process.stdin is None:
            raise HTTPException(409, "Runner job does not accept input")
        try:
            job.process.stdin.write(request.data)
            job.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise HTTPException(409, "Runner job input is closed") from exc
    return {"job_id": job.id, "accepted": True}


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, x_runner_key: str | None = Header(None)):
    """
    Cancels a job by its ID. This function updates the job's status to "cancelled",
    terminates the associated process, and releases resources.

    Args:
        job_id (str): Unique identifier for the job to cancel.
        x_runner_key (str | None): API key for authentication, extracted from the request header.
    """
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
