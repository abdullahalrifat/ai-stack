from pathlib import Path

from app.llm import calibration_api
from app.llm.calibration import RuntimeCalibration, RuntimeObservation


def test_calibrated_model_uses_runtime_evidence(tmp_path: Path, monkeypatch):
    path = tmp_path / "runtime.jsonl"
    store = RuntimeCalibration(path, min_samples=2)
    for _ in range(2):
        store.record(
            RuntimeObservation(
                model="local", task="code", success=True, quality=0.95
            )
        )
    monkeypatch.setenv("JARVIS_RUNTIME_CALIBRATION_FILE", str(path))
    assert calibration_api.calibrated_model(["local", "cloud"], "code") == "local"
