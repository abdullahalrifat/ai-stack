"""Fail-closed per-task isolation policy.

This module makes isolation requirements part of the task contract rather than
an ambient worker configuration. It validates workspace ownership, resource
limits and egress policy before a task is admitted. A deployment must provide
an actual enforcement backend for network allowlists; merely validating the
list is never reported as isolation.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


_HOST = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class ResourceLimits:
    cpu_seconds: int = 90
    memory_mb: int = 2048
    pids: int = 256
    disk_mb: int = 4096
    open_files: int = 256

    def validate(self) -> None:
        if not 1 <= self.cpu_seconds <= 86_400:
            raise ValueError("cpu_seconds must be between 1 and 86400")
        if not 128 <= self.memory_mb <= 131_072:
            raise ValueError("memory_mb is outside the supported safety range")
        if not 16 <= self.pids <= 32_768:
            raise ValueError("pids is outside the supported safety range")
        if not 64 <= self.disk_mb <= 1_048_576:
            raise ValueError("disk_mb is outside the supported safety range")
        if not 16 <= self.open_files <= 65_536:
            raise ValueError("open_files is outside the supported safety range")


@dataclass(frozen=True)
class EgressPolicy:
    allowed_hosts: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = (443,)
    allow_dns: bool = True

    def validate(self) -> None:
        if any(port < 1 or port > 65535 for port in self.allowed_ports):
            raise ValueError("egress ports must be valid TCP/UDP ports")
        for value in self.allowed_hosts:
            candidate = value.strip().lower()
            try:
                ipaddress.ip_network(candidate, strict=False)
                continue
            except ValueError:
                pass
            if not _HOST.fullmatch(candidate):
                raise ValueError(f"invalid egress host: {value}")

    def allows(self, host: str, port: int) -> bool:
        self.validate()
        if port not in self.allowed_ports:
            return False
        candidate = host.strip().lower().rstrip(".")
        for entry in self.allowed_hosts:
            entry = entry.lower().rstrip(".")
            try:
                if ipaddress.ip_address(candidate) in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                if candidate == entry or candidate.endswith("." + entry):
                    return True
        return False


@dataclass(frozen=True)
class TaskIsolationPolicy:
    task_id: str
    workspace: Path
    read_only_base: bool = True
    writable_paths: tuple[str, ...] = ()
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    egress: EgressPolicy = field(default_factory=EgressPolicy)
    network_backend: str = "none"

    def validate(self) -> None:
        if not self.task_id or len(self.task_id) > 128:
            raise ValueError("invalid task_id")
        root = self.workspace.resolve()
        if not root.is_dir():
            raise ValueError("task workspace must be an existing directory")
        self.resources.validate()
        self.egress.validate()
        if self.egress.allowed_hosts and self.network_backend in {"", "none"}:
            raise RuntimeError(
                "egress allowlist requested but no enforcing network backend is configured"
            )
        if self.network_backend not in {"none", "firejail", "nftables"}:
            raise ValueError("unsupported network enforcement backend")
        for item in self.writable_paths:
            path = (root / item).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise PermissionError("writable path escapes task workspace") from exc

    def command_environment(self) -> dict[str, str]:
        self.validate()
        return {
            "JARVIS_TASK_ID": self.task_id,
            "JARVIS_NETWORK_POLICY": "allowlist" if self.egress.allowed_hosts else "deny-all",
            "JARVIS_NETWORK_BACKEND": self.network_backend,
        }


def policy_from_mapping(value: dict) -> TaskIsolationPolicy:
    resources = value.get("resources") or {}
    egress = value.get("egress") or {}
    policy = TaskIsolationPolicy(
        task_id=str(value.get("task_id", "")),
        workspace=Path(str(value.get("workspace", ""))),
        read_only_base=bool(value.get("read_only_base", True)),
        writable_paths=tuple(str(item) for item in value.get("writable_paths", ())),
        resources=ResourceLimits(**{k: resources[k] for k in ResourceLimits.__dataclass_fields__ if k in resources}),
        egress=EgressPolicy(
            allowed_hosts=tuple(str(item) for item in egress.get("allowed_hosts", ())),
            allowed_ports=tuple(int(item) for item in egress.get("allowed_ports", (443,))),
            allow_dns=bool(egress.get("allow_dns", True)),
        ),
        network_backend=str(value.get("network_backend", "none")),
    )
    policy.validate()
    return policy


def allowed_url(policy: EgressPolicy, url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return policy.allows(parsed.hostname, port)
