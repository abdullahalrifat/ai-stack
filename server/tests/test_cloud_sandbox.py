import pytest
from fastapi import HTTPException

from app.platform.cloud_sandbox import CloudSandboxPolicy


def test_cloud_sandbox_requires_explicit_image(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUD_SANDBOX_IMAGE", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(HTTPException, match="sandbox image") as error:
        CloudSandboxPolicy().argv(str(workspace), ["pytest"])
    assert error.value.status_code == 400


def test_cloud_sandbox_is_fail_closed_and_resource_constrained(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    argv = CloudSandboxPolicy(image="ai-stack-worker:tested").argv(
        str(workspace), ["pytest", "-q"]
    )
    assert argv[:5] == ["docker", "run", "--rm", "--init", "--read-only"]
    assert "--user" in argv and "65532:65532" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "no-new-privileges" in argv
    assert "--cpus" in argv and "--memory" in argv and "--pids-limit" in argv
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "/workspace" in argv
    assert "docker.sock" not in " ".join(argv)


def test_cloud_sandbox_requires_policy_network_for_egress(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(HTTPException) as error:
        CloudSandboxPolicy(image="ai-stack-worker:tested", network="bridge").argv(
            str(workspace), ["pytest"]
        )
    assert error.value.status_code == 400
    assert "policy-enforced Docker network" in error.value.detail


def test_cloud_sandbox_accepts_only_policy_network_names(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    argv = CloudSandboxPolicy(
        image="ai-stack-worker:tested", network="policy-egress"
    ).argv(str(workspace), ["git", "status"])
    assert argv[argv.index("--network") + 1] == "policy-egress"


def test_cloud_sandbox_rejects_missing_workspace(tmp_path):
    with pytest.raises(HTTPException) as error:
        CloudSandboxPolicy(image="ai-stack-worker:tested").argv(
            str(tmp_path / "missing"), ["pytest"]
        )
    assert error.value.status_code == 400


def test_cloud_sandbox_rejects_docker_socket_configuration(monkeypatch):
    monkeypatch.setenv(
        "DOCKER_SOCKET_MOUNT", "/var/run/docker.sock:/var/run/docker.sock"
    )
    with pytest.raises(HTTPException) as error:
        CloudSandboxPolicy().validate_host_configuration()
    assert error.value.status_code == 503
