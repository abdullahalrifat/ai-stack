from pathlib import Path

import pytest

from app.isolation import EgressPolicy, ResourceLimits, TaskIsolationPolicy, allowed_url


def test_egress_policy_is_host_and_port_specific():
    policy = EgressPolicy(("pypi.org", "github.com"), (443,))
    assert policy.allows("pypi.org", 443)
    assert policy.allows("api.pypi.org", 443)
    assert not policy.allows("pypi.org", 80)
    assert not policy.allows("example.com", 443)
    assert allowed_url(policy, "https://github.com/org/repo")
    assert not allowed_url(policy, "http://github.com/org/repo")


def test_task_policy_fails_closed_without_enforcement_backend(tmp_path: Path):
    policy = TaskIsolationPolicy(
        task_id="task-1",
        workspace=tmp_path,
        egress=EgressPolicy(("github.com",), (443,)),
    )
    with pytest.raises(RuntimeError, match="no enforcing network backend"):
        policy.validate()


def test_task_policy_rejects_workspace_escape(tmp_path: Path):
    policy = TaskIsolationPolicy(
        task_id="task-1",
        workspace=tmp_path,
        writable_paths=("../outside",),
        network_backend="firejail",
    )
    with pytest.raises(PermissionError):
        policy.validate()


def test_resource_limits_have_sane_bounds():
    ResourceLimits().validate()
    with pytest.raises(ValueError):
        ResourceLimits(memory_mb=1).validate()
