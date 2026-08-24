from pydantic import ValidationError
import pytest

from app.platform.router import CloudStateRequest, CloudTaskRequest


def test_cloud_state_request_rejects_unknown_state():
    with pytest.raises(ValidationError):
        CloudStateRequest(
            worker_id="worker",
            lease_id="lease",
            state="completed",
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
