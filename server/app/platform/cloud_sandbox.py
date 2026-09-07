"""AI Stack adapter for the shared jarvis-core sandbox primitive."""

from __future__ import annotations

import os

from jarvis_core.sandbox import (
    IsolationError,
    TaskResourceLimits,
    TaskSandboxPolicy,
    build_task_command,
    docker_available,
    validate_host_boundary,
)


class CloudSandboxPolicy:
    """Compatibility adapter preserving AI Stack's existing API."""

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
        self.image = image or os.getenv("CLOUD_SANDBOX_IMAGE", "ai-stack-worker:latest")
        self.cpu = cpu
        self.memory = memory
        self.pids = pids
        self.storage = storage
        self.network = network

    def _core_policy(self) -> TaskSandboxPolicy:
        network = "deny" if self.network == "none" else "egress"
        egress = None if network == "deny" else self.network
        return TaskSandboxPolicy(
            image=self.image,
            network=network,
            egress_network=egress,
            limits=TaskResourceLimits(
                cpus=self.cpu,
                memory=self.memory,
                pids=self.pids,
                disk=self.storage,
                tmpfs="256m",
            ),
        )

    def argv(self, workspace: str, command: list[str]) -> list[str]:
        return build_task_command(workspace=workspace, argv=command, policy=self._core_policy(), require_docker=False)

    def validate_host_configuration(self) -> None:
        validate_host_boundary()


__all__ = [
    "CloudSandboxPolicy",
    "IsolationError",
    "TaskResourceLimits",
    "TaskSandboxPolicy",
    "build_task_command",
    "docker_available",
]
