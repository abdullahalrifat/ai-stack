from uuid import uuid4

from pydantic import ValidationError
import pytest

from app.platform.router import (
    CloudCompleteRequest,
    CloudStateRequest,
    CloudTaskRequest,
    platform_capabilities,
)


def test_cloud_state_request_rejects_unknown_state():
    with pytest.raises(ValidationError):
        CloudStateRequest(
            worker_id="worker",
            lease_id=uuid4(),
            state="completed",
        )


def test_cloud_state_request_rejects_invalid_lease_uuid():
    with pytest.raises(ValidationError):
        CloudStateRequest(
            worker_id="worker",
            lease_id="not-a-uuid",
            state="running",
        )


def test_cloud_task_requires_one_workspace_source():
    with pytest.raises(ValidationError):
        CloudTaskRequest(task="x", workspace=None, repository_url=None)
    with pytest.raises(ValidationError):
        CloudTaskRequest(
            task="x",
            workspace="/workspace",
            repository_url="https://github.com/example/repo.git",
        )


def test_cloud_task_rejects_unsafe_git_ref_and_credentials():
    with pytest.raises(ValidationError):
        CloudTaskRequest(
            task="x",
            repository_url="https://github.com/example/repo.git",
            git_ref="--upload-pack=evil",
        )
    with pytest.raises(ValidationError):
        CloudTaskRequest(
            task="x",
            repository_url="https://user:secret@github.com/example/repo.git",
        )


def test_cloud_task_accepts_idempotency_and_exact_commit():
    request = CloudTaskRequest(
        task="x",
        repository_url="https://github.com/example/repo.git",
        git_ref="main",
        git_commit="abcdef1",
        idempotency_key="request-1234",
        model="coding",
    )
    assert request.git_ref == "main"
    assert request.git_commit == "abcdef1"
    assert request.idempotency_key == "request-1234"


def test_cloud_task_rejects_whitespace_idempotency_key_after_normalization():
    with pytest.raises(ValidationError):
        CloudTaskRequest(
            task="x",
            workspace="/workspace",
            idempotency_key="        ",
        )


def test_cloud_task_metadata_and_completion_payloads_are_bounded():
    with pytest.raises(ValidationError):
        CloudTaskRequest(
            task="x",
            workspace="/workspace",
            metadata={"blob": "x" * (65 * 1024)},
        )
    with pytest.raises(ValidationError):
        CloudCompleteRequest(
            worker_id="worker",
            lease_id=uuid4(),
            result={"blob": "x" * (1600 * 1024)},
        )



def test_platform_capabilities_advertise_protocol_not_client_dependency():
    capabilities = platform_capabilities()
    assert capabilities["service"] == "ai-stack"
    protocol = capabilities["protocols"]["cloud_execution"]
    assert protocol["versions"] == [1]
    assert protocol["proof_schema_versions"] == [1]
    assert protocol["lease_fencing"] is True
    # The contract is client-neutral: no Jarvis package or implementation name
    # is required to claim and complete work.
    assert "client" not in protocol
