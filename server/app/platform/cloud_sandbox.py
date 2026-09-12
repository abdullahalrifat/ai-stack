"""AI Stack adapter for the shared jarvis-core sandbox primitive."""

from __future__ import annotations

import os

from fastapi import HTTPException
from jarvis_core.sandbox import (
    IsolationError,
    SandboxError,
    TaskResourceLimits,
    TaskSandboxPolicy,
    build_task_command,
    docker_available,
    validate_host_boundary,
)


class CloudSandboxPolicy:
    """Compatibility adapter preserving AI Stack's existing HTTP-facing API."""

    def __init__(
        self,
        *,
        image: str | None = None,
        cpu: float = 2.0,
        memory: str = "2g",
        pids: int = 256,
        storage: str = "4g",
        network: str = "none",
    ) -> None:
        # Do not provide a fallback image: the shared core policy is fail-closed.
        self.image = image or os.getenv("CLOUD_SANDBOX_IMAGE", "").strip()
        self.cpu = cpu
        self.memory = memory
        self.pids = pids
        self.storage = storage
        self.network = network

    def _core_policy(self) -> TaskSandboxPolicy:
        network = "deny" if self.network == "none" else "egress"
        return TaskSandboxPolicy(
            image=self.image,
            network=network,
            egress_network=None if network == "deny" else self.network,
            limits=TaskResourceLimits(
                cpus=self.cpu,
                memory=self.memory,
                pids=self.pids,
                disk=self.storage,
                tmpfs="256m",
            ),
        )

    def argv(self, workspace: str, command: list[str]) -> list[str]:
        try:
            return build_task_command(
                workspace=workspace,
                argv=command,
                policy=self._core_policy(),
                require_docker=False,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except SandboxError as exc:
            raise HTTPException(400, str(exc)) from exc

    def validate_host_configuration(self) -> None:
        try:
            validate_host_boundary()
        except SandboxError as exc:
            raise HTTPException(503, str(exc)) from exc


__all__ = [
    "CloudSandboxPolicy",
    "IsolationError",
    "TaskResourceLimits",
    "TaskSandboxPolicy",
    "build_task_command",
    "docker_available",
]
