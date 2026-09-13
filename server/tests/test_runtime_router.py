from pathlib import Path

from app.llm.calibration import RuntimeCalibration, RuntimeObservation
from app.llm.runtime_router import select_calibrated_model


def test_runtime_route_facade_selects_measured_model(tmp_path: Path, monkeypatch):
    path = tmp_path / "runtime.jsonl"
    store = RuntimeCalibration(path, min_samples=2)
    for _ in range(2):
        store.record(
            RuntimeObservation(
                model="local", task="code", success=True, quality=0.95
            )
        )
    monkeypatch.setenv("JARVIS_RUNTIME_CALIBRATION_FILE", str(path))
    assert select_calibrated_model(["local", "cloud"], "code") == "local"
