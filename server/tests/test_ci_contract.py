from pathlib import Path

CORE_VERSION = "0.17.3"


def test_efficiency_defaults_are_consistent_across_config_and_compose():
    root = Path(__file__).parents[2]
    config = (root / "server/app/core/config.py").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yaml").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")

    expected = {
        'MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "24"))': "MAX_AGENT_STEPS",
        'TOKEN_RUN_INPUT_LIMIT = int(os.getenv("TOKEN_RUN_INPUT_LIMIT", "48000"))': "TOKEN_RUN_INPUT_LIMIT",
        'TOKEN_RUN_OUTPUT_LIMIT = int(os.getenv("TOKEN_RUN_OUTPUT_LIMIT", "8000"))': "TOKEN_RUN_OUTPUT_LIMIT",
        'TOKEN_TURN_INPUT_LIMIT = int(os.getenv("TOKEN_TURN_INPUT_LIMIT", "12000"))': "TOKEN_TURN_INPUT_LIMIT",
        'TOKEN_TURN_OUTPUT_LIMIT = int(os.getenv("TOKEN_TURN_OUTPUT_LIMIT", "4096"))': "TOKEN_TURN_OUTPUT_LIMIT",
        'TOKEN_AGENT_INPUT_LIMIT = int(os.getenv("TOKEN_AGENT_INPUT_LIMIT", "32000"))': "TOKEN_AGENT_INPUT_LIMIT",
        'TOKEN_AGENT_OUTPUT_LIMIT = int(os.getenv("TOKEN_AGENT_OUTPUT_LIMIT", "6000"))': "TOKEN_AGENT_OUTPUT_LIMIT",
    }
    for declaration, name in expected.items():
        assert declaration in config
        assert name in compose
        assert f"{name}=" in env_example


def test_ci_pins_coordinated_core_release():
    lockfile = Path(__file__).parents[1] / "requirements.lock"
    text = lockfile.read_text(encoding="utf-8")
    assert f"jarvis-agent-core=={CORE_VERSION}" in text
    assert "jarvis_cli" not in text
