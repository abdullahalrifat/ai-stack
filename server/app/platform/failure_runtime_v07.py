"""Runtime reconciliation from failed Runs into structured failure memory."""

from __future__ import annotations

import json

from .efficiency_v07 import failure_kind, retry_action, task_category
from .failure_store_v07 import failure_fingerprint
from .store import PlatformStore

_INSTALLED = False


def install_failure_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from app.agent import dispatch
    from app.platform import runtime

    base_tick = runtime.tick_platform_once
    base_packet = dispatch._evidence_packet

    def tick_platform_once():
        result = base_tick()
        store = runtime.get_run_store()
        platform = PlatformStore()
        recorded = 0
        for run in store.list_runs(250):
            if str(run.get("status") or "") != "failed":
                continue
            run_id = str(run.get("id") or "")
            if not run_id:
                continue
            detail = str(
                run.get("error")
                or run.get("failure")
                or run.get("result")
                or "run failed without a structured error"
            )
            task = str(run.get("task") or "")
            route = runtime._effective_route(run_id, str(run.get("model") or "unknown"))
            kind = failure_kind(detail)
            fingerprint = failure_fingerprint(kind, route, detail)
            try:
                if platform.record_failure_signature(
                    run_id=run_id,
                    fingerprint=fingerprint,
                    kind=kind,
                    category=task_category(task),
                    route=route,
                    detail=detail,
                    recovery=retry_action(kind, 1)["action"],
                ):
                    recorded += 1
            except Exception:
                continue
        result = dict(result)
        result["new_failure_signatures"] = recorded
        return result

    def evidence_packet(state):
        packet = base_packet(state)
        category = task_category(str(getattr(state, "user_message", "") or ""))
        try:
            hints = PlatformStore().failure_hints(category, 6)
        except Exception:
            hints = []
        if not hints:
            return packet
        suffix = json.dumps(hints, default=str, separators=(",", ":"))
        return packet + "\nMeasured prior failure patterns (do not repeat blindly): " + suffix[:1600]

    runtime.tick_platform_once = tick_platform_once
    dispatch._evidence_packet = evidence_packet
    _INSTALLED = True
