"""Executable chaos/soak scenarios for the fenced v0.9 cloud API."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import time
from typing import Any, Callable
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ChaosResult:
    scenario: str
    passed: bool
    detail: str
    duration_seconds: float


class CloudChaosClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        expected: set[int] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                status = response.status
                body = response.read(2 * 1024 * 1024)
        except HTTPError as exc:
            status = exc.code
            body = exc.read(2 * 1024 * 1024)
        allowed = expected or {200, 201}
        parsed = json.loads(body or b"{}")
        if status not in allowed:
            raise RuntimeError(f"unexpected HTTP {status}: {parsed}")
        return status, parsed


class ChaosRunner:
    def __init__(
        self,
        request: Callable[..., tuple[int, dict[str, Any]]],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.request = request
        self.sleep = sleep

    def _run(self, name: str, fn: Callable[[], str]) -> ChaosResult:
        started = time.monotonic()
        try:
            detail = fn()
            return ChaosResult(name, True, detail, time.monotonic() - started)
        except Exception as exc:
            return ChaosResult(name, False, str(exc), time.monotonic() - started)

    def idempotent_submission(self, task_payload: dict[str, Any]) -> ChaosResult:
        def scenario() -> str:
            key = "chaos-" + uuid.uuid4().hex
            body = {**task_payload, "idempotency_key": key}
            _, first = self.request("POST", "/platform/v09/cloud/tasks", body)
            _, second = self.request("POST", "/platform/v09/cloud/tasks", body)
            if str(first.get("id")) != str(second.get("id")):
                raise AssertionError("idempotency retry created a second task")
            return str(first.get("id"))
        return self._run("idempotent_submission", scenario)

    def cancellation_is_terminal(self, task_payload: dict[str, Any]) -> ChaosResult:
        def scenario() -> str:
            _, created = self.request("POST", "/platform/v09/cloud/tasks", task_payload)
            task_id = str(created["id"])
            self.request("POST", f"/platform/cloud/tasks/{task_id}/cancel")
            _, state = self.request("GET", f"/platform/cloud/tasks/{task_id}")
            if state.get("status") != "cancelled":
                raise AssertionError(f"cancel was not terminal: {state.get('status')}")
            return task_id
        return self._run("cancellation_is_terminal", scenario)

    def lease_expiry_reclaim(
        self,
        task_payload: dict[str, Any],
        worker_capabilities: dict[str, Any],
        *,
        lease_seconds: int = 15,
    ) -> ChaosResult:
        def scenario() -> str:
            _, created = self.request("POST", "/platform/v09/cloud/tasks", task_payload)
            task_id = str(created["id"])
            worker1 = {**worker_capabilities, "worker_id": "chaos-a", "lease_seconds": lease_seconds}
            _, claim1 = self.request("POST", "/platform/v09/cloud/claim", worker1)
            first = claim1.get("task") or {}
            if str(first.get("id")) != task_id:
                raise AssertionError("first worker did not claim expected task")
            old_lease = str(first.get("lease_id") or "")
            self.sleep(lease_seconds + 1)
            worker2 = {**worker_capabilities, "worker_id": "chaos-b", "lease_seconds": lease_seconds}
            _, claim2 = self.request("POST", "/platform/v09/cloud/claim", worker2)
            second = claim2.get("task") or {}
            if str(second.get("id")) != task_id:
                raise AssertionError("expired task was not reclaimed")
            if str(second.get("lease_id") or "") == old_lease:
                raise AssertionError("reclaim reused stale lease fence")
            stale = {
                "worker_id": "chaos-a",
                "lease_id": old_lease,
                "result": {"status": "completed"},
            }
            status, _ = self.request(
                "POST",
                f"/platform/cloud/tasks/{task_id}/complete",
                stale,
                expected={409},
            )
            if status != 409:
                raise AssertionError("stale completion was accepted")
            return task_id
        return self._run("lease_expiry_reclaim", scenario)

    def soak(
        self,
        task_payload: dict[str, Any],
        worker_capabilities: dict[str, Any],
        *,
        iterations: int = 20,
    ) -> dict[str, Any]:
        results: list[ChaosResult] = []
        for _ in range(max(1, min(iterations, 1000))):
            results.append(self.idempotent_submission(task_payload))
            results.append(self.cancellation_is_terminal(task_payload))
        return {
            "iterations": iterations,
            "passed": sum(item.passed for item in results),
            "failed": sum(not item.passed for item in results),
            "results": [asdict(item) for item in results],
            "worker_capabilities": worker_capabilities,
        }
