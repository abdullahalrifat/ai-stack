"""Foreground-client lease expiry for durable agent runs."""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool

from .events import get_event_publisher
from .store import get_run_store

logger = logging.getLogger(__name__)
CLIENT_LEASE_SWEEP_SECONDS = 2
CLIENT_LEASE_SWEEP_BATCH_SIZE = 100


@dataclass
class LeaseSweepMetrics:
    sweeps: int = 0
    failures: int = 0
    expired_runs: int = 0
    renewal_attempts: int = 0
    renewal_failures: int = 0
    last_batch_size: int = 0
    last_duration_seconds: float = 0
    max_overdue_seconds: float = 0
    last_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def success(self, runs: list[dict[str, Any]], duration: float) -> None:
        overdue = [float(run.get("overdue_seconds") or 0) for run in runs]
        with self._lock:
            self.sweeps += 1
            self.expired_runs += len(runs)
            self.last_batch_size = len(runs)
            self.last_duration_seconds = duration
            self.max_overdue_seconds = max(
                [self.max_overdue_seconds, *overdue],
            )
            self.last_error = None

    def failure(self, error: Exception, duration: float) -> None:
        with self._lock:
            self.sweeps += 1
            self.failures += 1
            self.last_batch_size = 0
            self.last_duration_seconds = duration
            self.last_error = f"{type(error).__name__}: {error}"[:500]

    def renewal(self, succeeded: bool) -> None:
        with self._lock:
            self.renewal_attempts += 1
            if not succeeded:
                self.renewal_failures += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sweeps_total": self.sweeps,
                "failures_total": self.failures,
                "expired_runs_total": self.expired_runs,
                "renewal_attempts_total": self.renewal_attempts,
                "renewal_failures_total": self.renewal_failures,
                "last_batch_size": self.last_batch_size,
                "last_duration_seconds": self.last_duration_seconds,
                "max_overdue_seconds": self.max_overdue_seconds,
                "last_error": self.last_error,
                "batch_size": CLIENT_LEASE_SWEEP_BATCH_SIZE,
                "interval_seconds": CLIENT_LEASE_SWEEP_SECONDS,
            }


lease_sweep_metrics = LeaseSweepMetrics()


def expire_abandoned_client_runs() -> int:
    """Cancel one replica-safe batch whose foreground client stopped renewing."""

    started = time.monotonic()
    store = get_run_store()
    try:
        expired = store.cancel_expired_client_runs(limit=CLIENT_LEASE_SWEEP_BATCH_SIZE)
        publisher = get_event_publisher()
        for run in expired:
            run_id = str(run["id"])
            event = store.append_event(
                run_id,
                "client_disconnected",
                {
                    "status": str(run["status"]),
                    "overdue_seconds": float(run.get("overdue_seconds") or 0),
                },
            )
            try:
                publisher.publish(event)
            except Exception:
                logger.exception(
                    "Could not publish client disconnect for run %s", run_id
                )
        lease_sweep_metrics.success(expired, time.monotonic() - started)
        return len(expired)
    except Exception as exc:
        lease_sweep_metrics.failure(exc, time.monotonic() - started)
        raise


async def monitor_client_leases() -> None:
    """Continuously expire leases without depending on an SSE disconnect."""

    while True:
        try:
            await run_in_threadpool(expire_abandoned_client_runs)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not sweep foreground client leases")
        await asyncio.sleep(CLIENT_LEASE_SWEEP_SECONDS)
