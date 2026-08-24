"""Background platform reconciliation, telemetry, and empirical routing."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import re
from typing import Any

from jarvis_core import RouteCandidate

from app.agent.service import submit_run
from app.runs.store import get_run_store

from .store import PlatformStore
from .telemetry import telemetry

logger = logging.getLogger(__name__)
PLATFORM_RECONCILE_SECONDS = 5
TERMINAL = {"completed", "awaiting_approval", "failed", "discarded", "cancelled"}


def install_agent_telemetry() -> None:
    """Wrap the existing executor/tool registry without creating a second engine."""
    from app.agent import executor, service
    from app.tools.registry import registry

    if getattr(executor, "_V06_TELEMETRY_INSTALLED", False):
        return
    original_plan = executor.execute_plan
    original_tool_execute = registry.execute

    def traced_plan(state, *args, **kwargs):
        with telemetry.span(
            "jarvis.agent.execute_plan",
            model=str(getattr(state, "model", "unknown")),
            workspace=str(getattr(state, "workspace", "")),
            allow_write=bool(getattr(state, "allow_write", False)),
            prompt_mode=str(getattr(state, "prompt_mode", "")),
        ):
            return original_plan(state, *args, **kwargs)

    def traced_tool(name: str, args: dict):
        with telemetry.span(
            "jarvis.agent.tool",
            tool=name,
            argument_keys=sorted(str(key) for key in args),
        ):
            return original_tool_execute(name, args)

    executor.execute_plan = traced_plan
    service.execute_plan = traced_plan
    registry.execute = traced_tool
    executor._V06_TELEMETRY_INSTALLED = True


def _category(task: str) -> str:
    text = task.casefold()
    if re.search(r"\b(test|pytest|unit test|integration test)\b", text):
        return "tests"
    if re.search(r"\b(security|auth|credential|permission|secret)\b", text):
        return "security"
    if re.search(r"\b(browser|frontend|ui|react|playwright)\b", text):
        return "frontend"
    if re.search(r"\b(research|web|latest|current)\b", text):
        return "research"
    return "code"


def _latency_ms(run: dict[str, Any]) -> float:
    start = run.get("started_at") or run.get("created_at")
    end = run.get("completed_at") or run.get("updated_at")
    if isinstance(start, datetime) and isinstance(end, datetime):
        return max(0.0, (end - start).total_seconds() * 1000)
    return 0.0


def _effective_route(run_id: str, fallback: str) -> str:
    """Prefer the effective model recorded after Auto routing, not requested alias."""
    try:
        events = get_run_store().events_after(run_id, 0)
    except Exception:
        return fallback
    for event in reversed(events):
        if str(event.get("event_type")) != "route_selected":
            continue
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            model = str(payload.get("model") or "").strip()
            if model:
                return model
    return fallback


def _incorrect_completion(run_id: str, status: str) -> bool:
    if status not in {"completed", "awaiting_approval"}:
        return False
    try:
        return any(
            str(event.get("event_type")) == "answer_audit_failed"
            for event in get_run_store().events_after(run_id, 0)
        )
    except Exception:
        return False


def tick_platform_once() -> dict[str, int]:
    platform = PlatformStore()
    scheduled = observed = 0
    for schedule in platform.due_schedules():
        payload = dict(schedule.get("payload") or {})
        try:
            run_id = get_run_store().create_run(
                task=str(payload["task"]),
                model=str(payload.get("model") or "orchestrator"),
                workspace=str(payload["workspace"]),
                conversation_id=payload.get("conversation_id"),
                document_scope=payload.get("document_scope"),
                project_id=payload.get("project_id"),
                allow_write=bool(payload.get("allow_write", False)),
                client_id=None,
            )
            submit_run(run_id)
            scheduled += 1
        except Exception:
            logger.exception(
                "Could not submit scheduled agent run %s", schedule.get("id")
            )

    for run in get_run_store().list_runs(250):
        status = str(run.get("status") or "")
        if status not in TERMINAL:
            continue
        run_id = str(run["id"])
        incorrect = _incorrect_completion(run_id, status)
        success = status in {"completed", "awaiting_approval"} and not incorrect
        route = _effective_route(run_id, str(run.get("model") or "unknown"))
        try:
            if platform.record_route_observation(
                run_id=run_id,
                route=route,
                category=_category(str(run.get("task") or "")),
                success=success,
                incorrect_completion=incorrect,
                latency_ms=_latency_ms(run),
            ):
                observed += 1
        except Exception:
            logger.warning(
                "Could not record route observation for run %s",
                run.get("id"),
                exc_info=True,
            )
    return {"scheduled_runs": scheduled, "new_observations": observed}


async def monitor_platform() -> None:
    while True:
        try:
            with telemetry.span("jarvis.platform.reconcile"):
                await asyncio.to_thread(tick_platform_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Platform reconciliation failed")
        await asyncio.sleep(PLATFORM_RECONCILE_SECONDS)


def install_empirical_routing() -> None:
    """Patch candidate loading so measured outcomes influence expert route scores."""
    from app.agent import quality

    if getattr(quality, "_V06_CALIBRATION_INSTALLED", False):
        return
    original = quality.configured_candidates

    def configured_candidates(default_model: str):
        base = original(default_model)
        try:
            scores = PlatformStore().route_scores("code")
        except Exception:
            return base
        measured = {
            str(row["route"]): row
            for row in scores
            if int(row.get("samples") or 0) >= 3
        }
        calibrated = []
        for candidate in base:
            row = measured.get(candidate.profile) or measured.get(candidate.model)
            if not row:
                calibrated.append(candidate)
                continue
            success = max(0.0, min(1.0, float(row.get("success_rate") or 0)))
            incorrect = max(
                0.0, min(1.0, float(row.get("incorrect_rate") or 0))
            )
            latency_ms = max(0.0, float(row.get("latency_ms") or 0))
            latency_score = 1.0 / (1.0 + latency_ms / 10000.0)
            calibrated.append(
                RouteCandidate(
                    profile=candidate.profile,
                    model=candidate.model,
                    provider=candidate.provider,
                    quality=max(0.0, success - incorrect * 0.75),
                    tool_success=max(candidate.tool_success * 0.25, success),
                    structured_success=candidate.structured_success,
                    latency=latency_score,
                    cost=candidate.cost,
                    roles=candidate.roles,
                )
            )
        return tuple(calibrated)

    quality.configured_candidates = configured_candidates
    quality._V06_CALIBRATION_INSTALLED = True
