"""Safe process-group execution with cooperative cancellation and escalation."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .cancellation import cancellation_requested
from .exceptions import ProcessKillFailed, RunCancelled

PROCESS_POLL_SECONDS = 0.20
PROCESS_TERMINATE_GRACE_SECONDS = 3.0


def terminate_process_group(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float = PROCESS_TERMINATE_GRACE_SECONDS,
) -> bool:
    if process.poll() is not None:
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    try:
        process.wait(timeout=grace_seconds)
        return True
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        try:
            process.wait(timeout=grace_seconds)
            return True
        except subprocess.TimeoutExpired:
            return False


def run_cancellable(
    command: list[str],
    *,
    cwd: str | Path | None = None,
    input: str | None = None,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one command in its own session and stop its entire child tree."""

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    pending_input = input
    while True:
        if cancellation_requested():
            terminated = terminate_process_group(process)
            if not terminated:
                raise ProcessKillFailed(
                    "Could not terminate cancelled process group after SIGKILL"
                )
            process.communicate()
            raise RunCancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminated = terminate_process_group(process)
            if not terminated:
                raise ProcessKillFailed(
                    "Could not terminate timed-out process group after SIGKILL"
                )
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output=stdout,
                stderr=stderr,
            )
        try:
            stdout, stderr = process.communicate(
                input=pending_input,
                timeout=min(PROCESS_POLL_SECONDS, remaining),
            )
            return subprocess.CompletedProcess(
                command,
                process.returncode,
                stdout,
                stderr,
            )
        except subprocess.TimeoutExpired:
            # communicate() retains partially read output across retries.
            pending_input = None
