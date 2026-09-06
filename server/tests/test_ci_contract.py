from pathlib import Path


CORE_VERSION = "0.9.4"


def test_ci_pins_coordinated_core_release():
    lockfile = Path(__file__).parents[1] / "requirements.lock"
    text = lockfile.read_text(encoding="utf-8")
    assert f"jarvis_agent_core-{CORE_VERSION}-py3-none-any.whl" in text
    assert f"releases/download/v{CORE_VERSION}/" in text
