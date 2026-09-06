from pathlib import Path


def test_ci_pins_coordinated_core_release():
    lockfile = Path(__file__).parents[1] / "requirements.lock"
    text = lockfile.read_text(encoding="utf-8")
    assert "jarvis_agent_core-0.9.2-py3-none-any.whl" in text
