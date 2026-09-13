from pathlib import Path

from app.llm.calibration import RuntimeCalibration, RuntimeObservation


def test_runtime_calibration_requires_multiple_samples(tmp_path: Path):
    store = RuntimeCalibration(tmp_path / "calibration.jsonl", min_samples=3)
    store.record(
        RuntimeObservation(model="local", task="code", success=True, quality=1.0)
    )
    assert store.select(["local"], "code") is None


def test_runtime_calibration_prefers_quality_floor(tmp_path: Path):
    store = RuntimeCalibration(tmp_path / "calibration.jsonl", min_samples=2)
    for _ in range(2):
        store.record(
            RuntimeObservation(
                model="local",
                task="code",
                success=True,
                quality=0.95,
                latency_ms=100,
                cost_usd=0.0,
            )
        )
        store.record(
            RuntimeObservation(
                model="cloud",
                task="code",
                success=True,
                quality=0.60,
                latency_ms=50,
                cost_usd=0.01,
            )
        )
    assert store.select(["local", "cloud"], "code") == "local"


def test_runtime_calibration_is_backward_tolerant(tmp_path: Path):
    path = tmp_path / "calibration.jsonl"
    path.write_text(
        '{"model":"local","task":"code","success":true,"quality":0.9}\n',
        encoding="utf-8",
    )
    store = RuntimeCalibration(path)
    assert store.observations()[0].source == "runtime"
    assert store.observations()[0].recorded_at == 0.0
