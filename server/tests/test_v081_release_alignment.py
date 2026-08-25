from pathlib import Path


CORE_VERSION = "0.9.1"
CORE_COMMIT = "c30ffc900779caa07f9945f96a78e57828cb2aff"
SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent


def test_server_package_image_and_ci_use_same_core_contract():
    requirements = (SERVER_ROOT / "requirements.txt").read_text(encoding="utf-8")
    lockfile = (SERVER_ROOT / "requirements.lock").read_text(encoding="utf-8")
    dockerfile = (SERVER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/python-package.yml").read_text(
        encoding="utf-8"
    )

    expected_source = (
        "git+https://github.com/abdullahalrifat/jarvis-core.git@" + CORE_COMMIT
    )
    for content in (requirements, lockfile):
        assert expected_source in content
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
