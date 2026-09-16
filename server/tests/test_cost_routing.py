from app.llm.routing import route_model


def test_auto_defaults_to_local_tier(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_MODELS", "quick,coder")
    monkeypatch.delenv("JARVIS_CHEAP_MODELS", raising=False)
    monkeypatch.delenv("JARVIS_FRONTIER_MODELS", raising=False)
    assert route_model(["quick", "coder"], complexity=0.1, risk=0.1) in {"quick", "coder"}


def test_complex_request_uses_configured_cheap_tier(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_MODELS", "quick")
    monkeypatch.setenv("JARVIS_CHEAP_MODELS", "cloud-cheap")
    monkeypatch.setenv("JARVIS_FRONTIER_MODELS", "cloud-frontier")
    assert route_model(["quick", "cloud-cheap", "cloud-frontier"], complexity=0.9, uncertainty=0.8) == "cloud-cheap"


def test_security_request_uses_frontier(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_MODELS", "quick")
    monkeypatch.setenv("JARVIS_CHEAP_MODELS", "cloud-cheap")
    monkeypatch.setenv("JARVIS_FRONTIER_MODELS", "cloud-frontier")
    assert route_model(["quick", "cloud-cheap", "cloud-frontier"], security_sensitive=True) == "cloud-frontier"
