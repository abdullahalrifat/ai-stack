"""Private command runner that can see only disposable agent worktrees."""

import hmac
import os
import resource
import shlex
import subprocess
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

SANDBOX_ROOT = Path(os.getenv("SANDBOX_ROOT", "/sandboxes")).resolve()
RUNNER_API_KEY = os.getenv("RUNNER_API_KEY", "")
COMMAND_TIMEOUT_SECONDS = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "120"))
CPU_SECONDS = int(os.getenv("RUNNER_CPU_SECONDS", "90"))
MEMORY_MB = int(os.getenv("RUNNER_MEMORY_MB", "2048"))
MAX_OPEN_FILES = int(os.getenv("RUNNER_MAX_OPEN_FILES", "256"))
ALLOWED = {
    item.strip()
    for item in os.getenv(
        "ALLOWED_COMMANDS",
        "git,pytest,python,python3,npm,node,make,mypy,ruff,black,flake8",
    ).split(",")
    if item.strip()
}

app = FastAPI(title="Private agent runner")


class ExecuteRequest(BaseModel):
    command: str
    directory: str


def _preexec() -> None:
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    memory = MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_NOFILE, (MAX_OPEN_FILES, MAX_OPEN_FILES))


@app.post("/execute")
def execute(request: ExecuteRequest, x_runner_key: str | None = Header(None)):
    if (
        not RUNNER_API_KEY
        or not x_runner_key
        or not hmac.compare_digest(x_runner_key, RUNNER_API_KEY)
    ):
        raise HTTPException(401, "Invalid runner key")
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
    if SANDBOX_ROOT not in directory.parents:
        raise HTTPException(403, "Runner accepts only sandbox worktrees")
    if not directory.is_dir():
        raise HTTPException(400, "Sandbox directory does not exist")
    try:
        result = subprocess.run(
            parts,
            cwd=directory,
            text=True,
            capture_output=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": "/tmp/runner",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            preexec_fn=_preexec,
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "output": "Command timed out"}
    return {
        "exit_code": result.returncode,
        "output": (result.stdout + "\n" + result.stderr).strip()[-30_000:],
    }
