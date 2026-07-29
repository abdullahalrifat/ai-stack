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
from app.core.config import POSTGRES_URL, WORKSPACE_ROOTS, validate_settings
from app.runs.client_leases import monitor_client_leases
from app.runs.sandbox import remove_sandbox
from app.runs.store import get_run_store
from app.tools.registry import registry

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_settings()
    logger.info("REGISTERED TOOLS: %s", registry.list_tools())
    logger.info("WORKSPACE ROOTS: %s", [str(root) for root in WORKSPACE_ROOTS])

    client_lease_monitor = None
    if POSTGRES_URL:
        store = get_run_store()
        store.initialize()
        logger.info("Durable run store initialized.")
        queued, _interrupted = store.recover_interrupted_runs()
        # Terminal rows retain their sandbox path until cleanup succeeds.
        # This makes reconciliation repeatable across consecutive crashes.
        for run in store.sandboxes_needing_cleanup():
            try:
                remove_sandbox(run["repository_path"], run["sandbox_path"])
                store.mark_sandbox_cleaned(str(run["id"]))
            except Exception:
                logger.exception(
                    "Could not reconcile abandoned sandbox for run %s", run["id"]
                )
        for run_id in queued:
            logger.info("Resuming queued run %s after service restart", run_id)
            submit_run(run_id)
        client_lease_monitor = asyncio.create_task(monitor_client_leases())
    else:
        logger.warning("POSTGRES_URL not set; durable /runs endpoints are unavailable.")
    try:
        yield
    finally:
        if client_lease_monitor is not None:
            client_lease_monitor.cancel()
            with suppress(asyncio.CancelledError):
                await client_lease_monitor
        shutdown_run_executor()
        if POSTGRES_URL:
            get_run_store().close()


app = FastAPI(
    title="Local AI Engineering Agent",
    description="Private autonomous coding agent running in homelab",
    version="3.0",
    lifespan=lifespan,
)
app.include_router(router)
