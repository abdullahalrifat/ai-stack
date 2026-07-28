"""Small standard-library HTTP client for the agent Runs API."""

from __future__ import annotations

import json
from collections.abc import Iterator
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class APIError(RuntimeError):
    """A useful error returned by the agent service or transport."""


class AgentClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30,
        stream_timeout: float = 90,
        opener=urlopen,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.stream_timeout = stream_timeout
        self._opener = opener

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ):
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            return self._opener(
                request,
                timeout=self.timeout if timeout is None else timeout,
            )
        except HTTPError as exc:
            try:
                raw = exc.read().decode(errors="replace")
                parsed = json.loads(raw)
                detail = parsed.get("detail", raw)
            except (AttributeError, json.JSONDecodeError):
                detail = str(exc)
            raise APIError(f"{exc.code} {detail}") from exc
        except URLError as exc:
            raise APIError(f"Could not reach {self.base_url}: {exc.reason}") from exc

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._request(method, path, payload) as response:
            raw = response.read().decode()
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise APIError(f"Agent returned invalid JSON for {path}") from exc

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def workspaces(self) -> list[str]:
        return list(self.request("GET", "/workspace/choices").get("workspaces", []))

    def default_workspace(self) -> str:
        return str(self.request("GET", "/workspace/default")["workspace"])

    def projects(self) -> list[dict[str, Any]]:
        return list(self.request("GET", "/projects").get("projects", []))

    def create_run(
        self,
        task: str,
        *,
        workspace: str,
        conversation_id: str,
        allow_write: bool,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/runs",
            {
                "task": task,
                "workspace": workspace,
                "model": "orchestrator",
                "conversation_id": conversation_id,
                "project_id": project_id,
                "allow_write": allow_write,
            },
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.request("GET", f"/runs/{quote(run_id, safe='')}")

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        query = urlencode({"limit": limit})
        return list(self.request("GET", f"/runs?{query}").get("runs", []))

    def action(self, run_id: str, action: str) -> dict[str, Any]:
        if action not in {"approve", "discard", "cancel"}:
            raise ValueError(f"Unsupported run action: {action}")
        return self.request(
            "POST",
            f"/runs/{quote(run_id, safe='')}/{action}",
        )

    def stream_events(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> Iterator[dict[str, Any]]:
        query = urlencode({"after": after})
        response = self._request(
            "GET",
            f"/runs/{quote(run_id, safe='')}/events?{query}",
            timeout=self.stream_timeout,
        )
        data_lines: list[str] = []
        try:
            try:
                for raw_line in response:
                    line = raw_line.decode(errors="replace").rstrip("\r\n")
                    if not line:
                        if data_lines:
                            raw_data = "\n".join(data_lines)
                            data_lines.clear()
                            try:
                                yield json.loads(raw_data)
                            except json.JSONDecodeError as exc:
                                raise APIError(
                                    "Agent returned an invalid SSE event"
                                ) from exc
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if data_lines:
                    try:
                        yield json.loads("\n".join(data_lines))
                    except json.JSONDecodeError as exc:
                        raise APIError("Agent returned an invalid SSE event") from exc
            except (HTTPException, OSError, TimeoutError) as exc:
                raise APIError(f"Run event stream was interrupted: {exc}") from exc
        finally:
            response.close()