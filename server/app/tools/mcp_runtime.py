"""Supervised persistent MCP stdio processes with per-tool policy."""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
from threading import Lock, Thread
from time import monotonic
from typing import Any


class MCPProcess:
    def __init__(self, name: str, command: list[str], tools: dict[str, dict[str, Any]]) -> None:
        self.name = name
        self.command = command
        self.tools = tools
        self.process: subprocess.Popen[str] | None = None
        self.responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self.lock = Lock()
        self.next_id = 0
        self.initialized = False
        self.last_ok: float | None = None
        self.failures = 0

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            shell=False,
        )
        Thread(target=self._read, daemon=True).start()
        self.initialized = False

    def _read(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and "id" in item:
                self.responses.put(item)

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.start()
        assert self.process and self.process.stdin
        with self.lock:
            self.next_id += 1
            request_id = self.next_id
            self.process.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": params or {},
                    }
                )
                + "\n"
            )
            self.process.stdin.flush()
            deadline = monotonic() + 30
            while monotonic() < deadline:
                try:
                    response = self.responses.get(timeout=max(0.01, deadline - monotonic()))
                except queue.Empty:
                    break
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    self.failures += 1
                    raise RuntimeError(str(response["error"]))
                self.failures = 0
                self.last_ok = monotonic()
                return response.get("result")
        self.failures += 1
        raise TimeoutError(f"MCP request timed out: {self.name}/{method}")

    def initialize(self) -> None:
        if self.initialized:
            return
        self.request(
            "initialize",
            {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "jarvis-server", "version": "0.3"},
            },
        )
        assert self.process and self.process.stdin
        self.process.stdin.write(
            '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n'
        )
        self.process.stdin.flush()
        self.initialized = True

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        policy = self.tools.get(tool, {})
        if not bool(policy.get("allow", False)):
            raise PermissionError(f"MCP tool denied by policy: {self.name}/{tool}")
        self.initialize()
        return self.request("tools/call", {"name": tool, "arguments": arguments})

    def health(self) -> dict[str, Any]:
        return {
            "running": self.process is not None and self.process.poll() is None,
            "last_ok": self.last_ok,
            "failures": self.failures,
        }

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()


class MCPSupervisor:
    def __init__(self) -> None:
        self.processes: dict[str, MCPProcess] = {}
        self.reload()
        atexit.register(self.close)

    def reload(self) -> None:
        payload = json.loads(os.getenv("JARVIS_MCP_SERVERS_JSON", "{}"))
        self.close()
        self.processes = {}
        for name, item in payload.items():
            if isinstance(item, list):
                command, tools = item, {}
            else:
                if item.get("transport", "stdio") != "stdio":
                    continue
                command, tools = item.get("command", []), item.get("tools", {})
            if command:
                self.processes[str(name)] = MCPProcess(
                    str(name), [str(part) for part in command], dict(tools)
                )

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        process = self.processes.get(server)
        if process is None:
            raise LookupError(f"MCP server is unavailable: {server}")
        return process.call(tool, arguments)

    def health(self) -> dict[str, Any]:
        return {name: process.health() for name, process in self.processes.items()}

    def close(self) -> None:
        for process in getattr(self, "processes", {}).values():
            process.close()


supervisor = MCPSupervisor()
