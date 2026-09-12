"""Application construction and lifecycle wiring."""

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
from app.platform.engineering_router import router as engineering_router
from app.platform.failure_runtime_v07 import install_failure_runtime
from app.platform.failure_store_v07 import install_failure_store
from app.platform.lineage_runtime import install as install_lineage_runtime
from app.platform.router import router as platform_router
from app.platform.runtime import monitor_platform
from app.runs.client_leases import monitor_client_leases
from app.runs.sandbox import remove_sandbox
from app.runs.store import get_run_store
import app.tools.register  # noqa: F401
from app.tools.registry import registry

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
WORKER_RECONCILE_SECONDS = 5


def reconcile_runs_once() -> None:
    store = get_run_store()
    queued, _interrupted = store.recover_interrupted_runs()
    for run in store.sandboxes_needing_cleanup():
        try:
            remove_sandbox(run["repository_path"], run["sandbox_path"])
            store.mark_sandbox_cleaned(str(run["id"]))
        except Exception:
            logger.exception("Could not reconcile abandoned sandbox for run %s", run["id"])
    for run_id in queued:
        logger.info("Claiming queued/recovered run %s", run_id)
        submit_run(run_id)


async def monitor_worker_leases() -> None:
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
    CloudSandboxPolicy().validate_host_configuration()
    from app.llm.usage import install as install_llm_usage
    install_llm_usage()
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
        install_lineage_runtime()
        install_failure_runtime()
        reconcile_runs_once()
        from app.platform import runtime as platform_runtime
        platform_runtime.tick_platform_once()
        client_lease_monitor = asyncio.create_task(monitor_client_leases())
        worker_lease_monitor = asyncio.create_task(monitor_worker_leases())
        channel_delivery_monitor = asyncio.create_task(monitor_channel_deliveries())
        platform_monitor = asyncio.create_task(monitor_platform())
    else:
        logger.warning("POSTGRES_URL not set; durable /runs and /platform endpoints are unavailable.")
    try:
        yield
    finally:
        for monitor in (client_lease_monitor, channel_delivery_monitor, platform_monitor, worker_lease_monitor):
            if monitor is not None:
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
        shutdown_run_executor()
        if POSTGRES_URL:
            get_run_store().close()


app = FastAPI(
    title="Local AI Engineering Agent",
    description="Private autonomous coding agent running in homelab",
    version="3.4",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(channels_router)
app.include_router(platform_router)
app.include_router(engineering_router)
