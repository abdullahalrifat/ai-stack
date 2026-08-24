from pydantic import ValidationError
import pytest

from app.api.protocol import FEATURES
from app.platform import router as platform_router
from app.platform.router import CloudTaskRequest, submit_cloud_task


def test_portable_cloud_git_workspaces_are_advertised():
    assert "cloud_git_workspaces" in FEATURES


def test_cloud_request_requires_exactly_one_workspace_source():
    with pytest.raises(ValidationError, match="choose exactly one"):
        CloudTaskRequest(task="inspect")
    with pytest.raises(ValidationError, match="choose exactly one"):
        CloudTaskRequest(
            task="inspect",
            workspace="/workspace/repo",
            repository_url="https://github.com/example/repo.git",
        )


def test_cloud_git_workspace_rejects_unsafe_urls_and_refs():
    invalid = [
        {"repository_url": "http://github.com/example/repo.git"},
        {"repository_url": "https://token@github.com/example/repo.git"},
        {"repository_url": "https://github.com/example/repo.git?token=secret"},
        {
            "repository_url": "https://github.com/example/repo.git",
            "git_ref": "--upload-pack=evil",
        },
        {
            "repository_url": "https://github.com/example/repo.git",
            "git_ref": "feature/../../main",
        },
        {
            "repository_url": "https://github.com/example/repo.git",
            "git_commit": "not-a-commit",
        },
    ]
    for values in invalid:
        with pytest.raises(ValidationError):
            CloudTaskRequest(task="inspect", **values)


def test_git_coordinates_require_repository_url():
    with pytest.raises(ValidationError, match="require repository_url"):
        CloudTaskRequest(task="inspect", workspace="/workspace/repo", git_ref="main")


def test_cloud_git_submission_persists_portable_workspace_descriptor(monkeypatch):
    captured = {}

    class Store:
        def submit_cloud(self, payload):
            captured.update(payload)
            return {"id": "cloud-1", "payload": payload}

    monkeypatch.setattr(platform_router, "PlatformStore", lambda: Store())
    request = CloudTaskRequest(
        task="fix regression",
        repository_url="https://github.com/example/repo.git",
        git_ref="feature/fix",
        git_commit="abcdef1234567",
        allow_write=True,
        model="coder",
    )
    result = submit_cloud_task(request)

    assert result["id"] == "cloud-1"
    assert captured["workspace"] is None
    assert captured["workspace_spec"] == {
        "kind": "git",
        "repository_url": "https://github.com/example/repo.git",
        "git_ref": "feature/fix",
        "git_commit": "abcdef1234567",
    }
    assert captured["allow_write"] is True
    assert captured["model"] == "coder"
