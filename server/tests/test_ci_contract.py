from pathlib import Path


CORE_VERSION = "0.11.0"


def test_ci_pins_coordinated_core_release():
    lockfile = Path(__file__).parents[1] / "requirements.lock"
    text = lockfile.read_text(encoding="utf-8")
    assert f"jarvis-agent-core=={CORE_VERSION}" in text
    assert "jarvis_cli" not in text
