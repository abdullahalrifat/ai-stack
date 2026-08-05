from pathlib import Path

import pytest

from app.core.permissions import (
    FULL_WRITE,
    READ,
    SCOPED_WRITE,
    PermissionPolicy,
    active_policy,
    permissions_context,
    policy_for,
    sensitive_write_path,
)


class TestSensitiveWritePath:
    @pytest.mark.parametrize(
        "name",
        [
            ".env",
            ".env.local",
            ".env.production",
            ".env.development",
            ".env.staging",
            "credentials.json",
            "id_rsa",
            "id_ed25519",
            "id_dsa",
            "secrets.key",
            "cert.pem",
            "cert.p12",
            "cert.pfx",
            "deploy.KEY",
        ],
    )
    def test_denies_secret_shapes(self, name):
        assert sensitive_write_path(Path(name)) is True

    @pytest.mark.parametrize(
        "name",
        [
            ".env.example",
            ".env.sample",
            ".env.template",
            "README.md",
            "app/main.py",
            "docker-compose.yml",
            "settings.json",
        ],
    )
    def test_allows_safe_names(self, name):
        assert sensitive_write_path(Path(name)) is False


class TestCheckWrite:
    def test_read_scope_denies_all_writes(self, tmp_path):
        policy = PermissionPolicy(scope=READ)
        with pytest.raises(PermissionError):
            policy.check_write(tmp_path / "note.txt", tmp_path)

    def test_full_write_scope_allows_workspace_writes(self, tmp_path):
        policy = PermissionPolicy(scope=FULL_WRITE)
        assert policy.check_write(tmp_path / "note.txt", tmp_path) is None

    def test_full_write_scope_still_denies_sensitive_paths(self, tmp_path):
        policy = PermissionPolicy(scope=FULL_WRITE)
        with pytest.raises(PermissionError):
            policy.check_write(tmp_path / ".env", tmp_path)
        with pytest.raises(PermissionError):
            policy.check_write(tmp_path / "credentials.json", tmp_path)

    def test_scoped_write_denies_absent_roots(self, tmp_path):
        policy = PermissionPolicy(scope=SCOPED_WRITE)
        with pytest.raises(PermissionError):
            policy.check_write(tmp_path / "anywhere.txt", tmp_path)

    def test_scoped_write_allows_inside_configured_root(self, tmp_path):
        policy = PermissionPolicy(scope=SCOPED_WRITE, edit_roots=(Path("src"),))
        (tmp_path / "src").mkdir()
        assert policy.check_write(tmp_path / "src" / "mod.py", tmp_path) is None
        assert policy.check_write(tmp_path / "src" / "nested" / "mod.py", tmp_path) is None

    def test_scoped_write_denies_outside_configured_root(self, tmp_path):
        policy = PermissionPolicy(scope=SCOPED_WRITE, edit_roots=(Path("src"),))
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        with pytest.raises(PermissionError):
            policy.check_write(tmp_path / "tests" / "mod_test.py", tmp_path)

    def test_scoped_write_denies_paths_outside_workspace(self, tmp_path):
        policy = PermissionPolicy(scope=SCOPED_WRITE, edit_roots=(Path("src"),))
        outside = tmp_path / ".." / "elsewhere"
        with pytest.raises(PermissionError):
            policy.check_write(outside, tmp_path)


class TestCheckCommand:
    def test_empty_allowlist_permits_anything(self):
        policy = PermissionPolicy(scope=FULL_WRITE)
        assert policy.check_command("git") is None

    def test_allowlist_permits_approved_executable(self):
        policy = PermissionPolicy(command_allowlist=frozenset({"pytest"}))
        assert policy.check_command("pytest") is None

    def test_allowlist_denies_other_executables(self):
        policy = PermissionPolicy(command_allowlist=frozenset({"pytest"}))
        with pytest.raises(PermissionError):
            policy.check_command("git")


class TestPolicyFor:
    def test_read_only_request(self):
        policy = policy_for(allow_write=False)
        assert policy.scope == READ
        assert policy.allows_write() is False

    def test_write_without_edit_paths_is_full_write(self):
        policy = policy_for(allow_write=True)
        assert policy.scope == FULL_WRITE
        assert policy.allows_write() is True

    def test_write_with_edit_paths_is_scoped_write(self):
        policy = policy_for(allow_write=True, edit_paths=("src", "lib"))
        assert policy.scope == SCOPED_WRITE
        assert policy.edit_roots == (Path("src"), Path("lib"))

    def test_command_allowlist_is_propagated(self):
        policy = policy_for(allow_write=True, command_allowlist=("pytest",))
        assert policy.command_allowlist == frozenset({"pytest"})


class TestPermissionContext:
    def test_active_policy_default_is_full_write(self):
        assert active_policy().scope == FULL_WRITE

    def test_context_overrides_and_restores_policy(self):
        original = active_policy()
        restricted = PermissionPolicy(scope=READ)
        with permissions_context(restricted):
            assert active_policy() is restricted
        assert active_policy() is original

    def test_context_restores_after_exception(self):
        original = active_policy()
        with pytest.raises(RuntimeError):
            with permissions_context(PermissionPolicy(scope=READ)):
                raise RuntimeError("boom")
        assert active_policy() is original
