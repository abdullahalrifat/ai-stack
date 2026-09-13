from datetime import datetime, timezone
import hashlib
import hmac

import pytest

from app.channels.router import _verify_whatsapp
from app.core.claim_verification import verify_claims
from app.core.instructions import instruction_prompt, load_server_instructions
from app.llm.empirical import record_runtime_observation, select_empirical_route
from app.llm.routing import record_model_health, route_model
from app.runs.review import parse_review_hunks
from app.tools.mcp_runtime import MCPProcess


def test_claim_verification_ranks_primary_and_detects_conflict():
    now = datetime.now(timezone.utc).isoformat()
    result = verify_claims(
        [
            {
                "claim": "value increased",
                "sources": [
                    {
                        "url": "https://example.gov/report",
                        "kind": "government",
                        "published_at": now,
                        "supports": True,
                    },
                    {
                        "url": "https://forum.example/post",
                        "kind": "community",
                        "published_at": now,
                        "supports": False,
                    },
                ],
            }
        ]
    )[0]
    assert result["contradictions"]
    assert result["independent_domains"] == 2
    assert result["sources"][0]["kind"] == "government"


def test_instruction_precedence(tmp_path, monkeypatch):
    user = tmp_path / "user.md"
    user.write_text("user")
    monkeypatch.setenv("JARVIS_USER_INSTRUCTIONS", str(user))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("workspace")
    loaded = load_server_instructions(workspace)
    assert [item.content for item in loaded] == ["user", "workspace"]
    assert "Trusted instruction precedence" in instruction_prompt(workspace)


def test_health_routing_removes_open_circuit(monkeypatch):
    monkeypatch.delenv("JARVIS_MODEL_PROFILES_JSON", raising=False)
    for _ in range(3):
        record_model_health("bad", success=False)
    assert route_model(["bad", "good"]) == "good"


def test_empirical_route_requires_core_safeguards(tmp_path, monkeypatch):
    path = tmp_path / "routes.json"
    monkeypatch.setenv("JARVIS_ROUTE_CALIBRATION_FILE", str(path))
    assert select_empirical_route(["measured", "fallback"], "ci-triage", fallback="fallback") == "fallback"

    for _ in range(3):
        record_runtime_observation(
            route="measured",
            category="ci-triage",
            success=True,
            quality=0.95,
            latency_ms=100,
            source="real-jarvis-development",
        )

    assert select_empirical_route(["measured", "fallback"], "ci-triage", fallback="fallback") == "measured"


def test_unified_diff_is_split_into_reviewable_hunks():
    diff = """diff --git a/a.py b/a.py
index 111..222 100644
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old
+new
@@ -3 +3 @@
-x
+y
"""
    hunks = parse_review_hunks(diff)
    assert len(hunks) == 2
    assert hunks[0].path == "a.py"
    assert hunks[0].id != hunks[1].id


def test_mcp_policy_denies_unapproved_tool_without_starting_process():
    process = MCPProcess("docs", ["never-start"], {"search": {"allow": False}})
    with pytest.raises(PermissionError):
        process.call("search", {})


def test_whatsapp_hmac(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret")
    raw = b'{"ok":true}'
    signature = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
    _verify_whatsapp(raw, signature)
