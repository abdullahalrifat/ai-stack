import pytest
from fastapi import HTTPException

from app.api import dependencies


def test_bearer_auth_rejects_malformed_repeated_scheme(monkeypatch):
    monkeypatch.setattr(dependencies, "AGENT_API_KEY", "secret")
    monkeypatch.setattr(dependencies, "ALLOW_INSECURE_NO_AUTH", False)

    assert dependencies.verify_api_key("Bearer secret") is True
    assert dependencies.verify_api_key("bearer secret") is True
    for value in (
        "secret",
        "Bearer",
        "Bearer ",
        "Bearer Bearer secret",
        "Basic secret",
    ):
        with pytest.raises(HTTPException):
            dependencies.verify_api_key(value)
