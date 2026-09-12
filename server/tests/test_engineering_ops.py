from __future__ import annotations

import hashlib

from app.platform.engineering import identity_context, inference_status, soak_profile


def test_inference_status_never_exposes_remote_secret(monkeypatch):
    monkeypatch.setenv("INFERENCE_MODE", "hybrid")
    monkeypatch.setenv("HF_INFERENCE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("HF_API_KEY", "secret-value")
    status = inference_status()
    assert status["remote_configured"] is True
    assert "secret-value" not in str(status)


def test_identity_requires_tenant_when_enabled(monkeypatch):
    monkeypatch.setenv("TENANCY_ENABLED", "true")
    try:
        identity_context("authenticated", None, "operator")
    except ValueError as exc:
        assert "tenant" in str(exc).lower()
    else:
        raise AssertionError("tenant requirement was not enforced")


def test_identity_digest_is_stable(monkeypatch):
    monkeypatch.setenv("TENANCY_ENABLED", "true")
    first = identity_context("authenticated", "tenant-a", "user-a")
    second = identity_context("authenticated", "tenant-a", "user-a")
    assert first == second
    assert first["principal_digest"] == hashlib.sha256("tenant-a:user-a:authenticated".encode()).hexdigest()


def test_soak_profile_has_safe_defaults(monkeypatch):
    monkeypatch.delenv("SOAK_DURATION_HOURS", raising=False)
    profile = soak_profile()
    assert profile["duration_hours"] >= 24
    assert profile["max_failures"] >= 1
