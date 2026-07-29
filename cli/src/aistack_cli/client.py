"""Small standard-library HTTP client for the agent Runs API."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .protocol import (
    PROTOCOL_HEADER,
    PROTOCOL_VERSION,
    ProtocolError,
    validate_capabilities,
    validate_event,
)


class APIError(RuntimeError):
    """A useful error returned by the agent service or transport."""


MAX_SSE_EVENT_BYTES = max(
    16_384,
    int(os.getenv("AISTACK_MAX_SSE_EVENT_BYTES", "1048576")),
)
MAX_HTTP_RESPONSE_BYTES = max(
    65_536,
    int(os.getenv("AISTACK_MAX_HTTP_RESPONSE_BYTES", "4194304")),
)


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
        self._capabilities: dict[str, Any] | None = None

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
            PROTOCOL_HEADER: str(PROTOCOL_VERSION),
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
                raw = exc.read(MAX_HTTP_RESPONSE_BYTES + 1)[
                    :MAX_HTTP_RESPONSE_BYTES
                ].decode(errors="replace")
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
            raw_bytes = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        if len(raw_bytes) > MAX_HTTP_RESPONSE_BYTES:
            raise APIError(
                f"Agent response for {path} exceeded the configured "
                f"{MAX_HTTP_RESPONSE_BYTES}-byte safety limit."
            )
        raw = raw_bytes.decode(errors="replace")
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise APIError(f"Agent returned invalid JSON for {path}") from exc

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def capabilities(self) -> dict[str, Any]:
        capabilities = self.request("GET", "/capabilities")
        try:
            validate_capabilities(capabilities)
        except ProtocolError as exc:
            raise APIError(str(exc)) from exc
        self._capabilities = capabilities
        return capabilities

    def ensure_compatible(self, *required_features: str) -> None:
        capabilities = self._capabilities or self.capabilities()
        try:
            validate_capabilities(
                capabilities,
                required_features=tuple(required_features),
            )
        except ProtocolError as exc:
            raise APIError(str(exc)) from exc

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
        client_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "task": task,
            "workspace": workspace,
            "model": "orchestrator",
            "conversation_id": conversation_id,
            "project_id": project_id,
            "allow_write": allow_write,
        }
        if client_id:
            payload["client_id"] = client_id
        return self.request("POST", "/runs", payload)

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
        client_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        query_values: dict[str, str | int] = {"after": after}
        if client_id:
            query_values["client_id"] = client_id
        query = urlencode(query_values)
        response = self._request(
            "GET",
            f"/runs/{quote(run_id, safe='')}/events?{query}",
            timeout=self.stream_timeout,
        )
        data_lines: list[str] = []
        buffered_bytes = 0
        try:
            try:
                if hasattr(response, "readline"):

                    def response_lines():
                        while True:
                            raw = response.readline(MAX_SSE_EVENT_BYTES + 1)
                            if not raw:
                                return
                            if len(raw) > MAX_SSE_EVENT_BYTES:
                                raise APIError(
                                    "Agent SSE line exceeded the configured "
                                    f"{MAX_SSE_EVENT_BYTES}-byte safety limit."
                                )
                            yield raw

                    lines = response_lines()
                else:
                    lines = iter(response)
                for raw_line in lines:
                    line = raw_line.decode(errors="replace").rstrip("\r\n")
                    if not line:
                        if data_lines:
                            raw_data = "\n".join(data_lines)
                            data_lines.clear()
                            buffered_bytes = 0
                            try:
                                event = json.loads(raw_data)
                                validate_event(event)
                                yield event
                            except json.JSONDecodeError as exc:
                                raise APIError(
                                    "Agent returned an invalid SSE event"
                                ) from exc
                        continue
                    if line.startswith("data:"):
                        data = line[5:].lstrip()
                        buffered_bytes += len(data.encode("utf-8"))
                        if buffered_bytes > MAX_SSE_EVENT_BYTES:
                            raise APIError(
                                "Agent event exceeded the configured "
                                f"{MAX_SSE_EVENT_BYTES}-byte safety limit. "
                                "Increase AISTACK_MAX_SSE_EVENT_BYTES only for "
                                "a trusted server."
                            )
                        data_lines.append(data)
                if data_lines:
                    try:
                        event = json.loads("\n".join(data_lines))
                        validate_event(event)
                        yield event
                    except json.JSONDecodeError as exc:
                        raise APIError("Agent returned an invalid SSE event") from exc
            except ProtocolError as exc:
                raise APIError(str(exc)) from exc
            except (HTTPException, OSError, TimeoutError) as exc:
                raise APIError(f"Run event stream was interrupted: {exc}") from exc
        finally:
            response.close()
