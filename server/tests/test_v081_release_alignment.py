from pathlib import Path


CORE_VERSION = "0.9.2"
CORE_SHA256 = "0ff9b5cfba29dca8d05df69a48573c3a69cc73ca9654e7122411b89a489f1130"
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
        f"https://github.com/abdullahalrifat/jarvis-core/releases/download/"
        f"v{CORE_VERSION}/jarvis_agent_core-{CORE_VERSION}-py3-none-any.whl"
    )
    for content in (requirements, lockfile):
        assert expected_asset in content
        assert f"#sha256={CORE_SHA256}" in content
        assert "jarvis_cli" not in content
    assert f"m.version('jarvis-agent-core') == '{CORE_VERSION}'" in dockerfile
    assert workflow.count(f"m.version('jarvis-agent-core') == '{CORE_VERSION}'") >= 2


def test_supply_chain_publish_commands_are_separate_shell_commands():
    workflow = (REPO_ROOT / ".github/workflows/supply-chain.yml").read_text(
        encoding="utf-8"
    )
    assert "run: |" in workflow
    assert "\\n          docker push" not in workflow
    assert (
        'docker tag "ghcr.io/abdullahalrifat/ai-stack-server:${GITHUB_SHA}"' in workflow
    )
    assert (
        'docker push "ghcr.io/abdullahalrifat/ai-stack-server:${GITHUB_REF_NAME}"'
        in workflow
    )
