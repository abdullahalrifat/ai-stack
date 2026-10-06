from __future__ import annotations

import hashlib

from app.platform.engineering import identity_context, inference_status, soak_profile


def test_inference_status_uses_dedicated_gateway(monkeypatch):
    monkeypatch.setenv("INFERENCE_BASE_URL", "http://inference:8080/v1")
    monkeypatch.setattr(
        "app.platform.engineering.get_available_models",
        lambda: ["qwen3:1.7b", "qwen3:4b", "nomic-embed-text"],
    )
    status = inference_status()
    assert status["mode"] == "dedicated-inference"
    assert status["primary_provider"] == "jarvis-inference"
    assert status["remote_configured"] is False
    assert status["remote_base_url_configured"] is True
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
