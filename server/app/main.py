"""Application construction and lifecycle wiring.

Routes, request schemas, and API dependencies live in :mod:`app.api`.
Keeping this module small preserves the stable ``app.main:app`` deployment
target while preventing HTTP concerns from leaking into agent domains.
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.agent.service import shutdown_run_executor, submit_run
from app.api.routes import router
from app.channels.delivery import monitor_channel_deliveries
from app.channels.router import router as channels_router
from app.core.config import POSTGRES_URL, WORKSPACE_ROOTS, validate_settings
from app.platform.cloud_sandbox import CloudSandboxPolicy
from app.platform.efficiency_v07 import install_v07_efficiency
from app.platform.failure_runtime_v07 import install_failure_runtime
from app.platform.failure_store_v07 import install_failure_store
from app.platform.router import router as platform_router
from app.platform.runtime import monitor_platform
from app.runs.client_leases import monitor_client_leases
from app.runs.sandbox import remove_sandbox
from app.runs.store import get_run_store
import app.tools.register  # noqa: F401  (registers tools into the real registry)
from app.tools.registry import registry

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
WORKER_RECONCILE_SECONDS = 5


def reconcile_runs_once() -> None:
    """Recover expired workers and clean terminal sandboxes."""

    store = get_run_store()
    queued, _interrupted = store.recover_interrupted_runs()
    for run in store.sandboxes_needing_cleanup():
        try:
            remove_sandbox(run["repository_path"], run["sandbox_path"])
            store.mark_sandbox_cleaned(str(run["id"]))
        except Exception:
            logger.exception(
                "Could not reconcile abandoned sandbox for run %s", run["id"]
            )
    for run_id in queued:
        logger.info("Claiming queued/recovered run %s", run_id)
        submit_run(run_id)


async def monitor_worker_leases() -> None:
    """Continuously reconcile crashes and transient database restarts."""

    while True:
        try:
            await asyncio.to_thread(reconcile_runs_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not reconcile worker leases")
        await asyncio.sleep(WORKER_RECONCILE_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_settings()
    # Shared/untrusted workers must never start with an operator-provided
    # Docker socket mount. The policy is also used by the cloud worker launcher
    # when constructing per-task containers.
    CloudSandboxPolicy().validate_host_configuration()
    logger.info("REGISTERED TOOLS: %s", registry.list_tools())
    logger.info("WORKSPACE ROOTS: %s", [str(root) for root in WORKSPACE_ROOTS])

    client_lease_monitor = None
    worker_lease_monitor = None
    channel_delivery_monitor = None
    platform_monitor = None
    if POSTGRES_URL:
        store = get_run_store()
        store.initialize()
        logger.info("Durable run store initialized.")
        install_failure_store()
        install_v07_efficiency()
        install_failure_runtime()
        reconcile_runs_once()
        from app.platform import runtime as platform_runtime

        platform_runtime.tick_platform_once()
        client_lease_monitor = asyncio.create_task(monitor_client_leases())
        worker_lease_monitor = asyncio.create_task(monitor_worker_leases())
        channel_delivery_monitor = asyncio.create_task(monitor_channel_deliveries())
        platform_monitor = asyncio.create_task(monitor_platform())
    else:
        logger.warning(
            "POSTGRES_URL not set; durable /runs and /platform endpoints are unavailable."
        )
    try:
        yield
    finally:
        if client_lease_monitor is not None:
            client_lease_monitor.cancel()
            with suppress(asyncio.CancelledError):
                await client_lease_monitor
        if channel_delivery_monitor is not None:
            channel_delivery_monitor.cancel()
            with suppress(asyncio.CancelledError):
                await channel_delivery_monitor
        if platform_monitor is not None:
            platform_monitor.cancel()
            with suppress(asyncio.CancelledError):
                await platform_monitor
        if worker_lease_monitor is not None:
            worker_lease_monitor.cancel()
            with suppress(asyncio.CancelledError):
                await worker_lease_monitor
        shutdown_run_executor()
        if POSTGRES_URL:
            get_run_store().close()


app = FastAPI(
    title="Local AI Engineering Agent",
    description="Private autonomous coding agent running in homelab",
    version="3.2",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(channels_router)
app.include_router(platform_router)
