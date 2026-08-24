from pathlib import Path


CORE_VERSION = "0.8.0"
CORE_SHA256 = "d9569b69385e58a681ea01e900eb81c395d3f202a09a92878eb82bf4d4b8618a"
SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent


def test_server_package_image_and_ci_use_same_core_release():
    requirements = (SERVER_ROOT / "requirements.txt").read_text(encoding="utf-8")
    lockfile = (SERVER_ROOT / "requirements.lock").read_text(encoding="utf-8")
    dockerfile = (SERVER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/python-package.yml").read_text(
        encoding="utf-8"
    )

    expected_asset = (
        f"releases/download/v{CORE_VERSION}/"
        f"jarvis_agent_core-{CORE_VERSION}-py3-none-any.whl"
    )
    for content in (requirements, lockfile):
        assert expected_asset in content
        assert f"sha256={CORE_SHA256}" in content
        assert "v0.7.0" not in content
    assert f"m.version('jarvis-agent-core') == '{CORE_VERSION}'" in dockerfile
    assert workflow.count(f"m.version('jarvis-agent-core') == '{CORE_VERSION}'") >= 2
