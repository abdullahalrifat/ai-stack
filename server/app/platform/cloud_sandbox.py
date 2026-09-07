"""Fail-closed Docker policy for untrusted cloud worker executions."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException


class CloudSandboxPolicy:
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

    def argv(self, workspace: str, command: list[str]) -> list[str]:
        """Build a container command; reject unsafe network/workspace settings."""

        root = Path(workspace).resolve()
        if not root.is_dir():
            raise HTTPException(400, "cloud sandbox workspace does not exist")
        if self.network not in {"none"} and not self.network.startswith("policy-"):
            raise HTTPException(400, "cloud sandbox egress requires a policy-enforced network")
        if not command or command[0].startswith("-"):
            raise HTTPException(400, "cloud sandbox command is invalid")
        return [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--security-opt",
            "seccomp=default",
            "--network",
            self.network,
            "--cpus",
            str(self.cpu),
            "--memory",
            self.memory,
            "--memory-swap",
            self.memory,
            "--pids-limit",
            str(self.pids),
            "--storage-opt",
            f"size={self.storage}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=256m",
            "--mount",
            f"type=bind,src={root},dst=/workspace,rw",
            "--workdir",
            "/workspace",
            "--pull=never",
            self.image,
            *command,
        ]

    def validate_host_configuration(self) -> None:
        """Fail closed if an operator attempts to expose the Docker socket."""

        if os.getenv("DOCKER_SOCKET_MOUNT", "").strip():
            raise HTTPException(503, "Docker socket exposure is forbidden for cloud workers")
