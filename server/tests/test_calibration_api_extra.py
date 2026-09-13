from pathlib import Path

from app.llm.calibration import RuntimeCalibration, RuntimeObservation


def test_leaderboard_reports_samples(tmp_path: Path):
    store = RuntimeCalibration(tmp_path / "runtime.jsonl")
    for _ in range(3):
        store.record(
            RuntimeObservation(
                model="local", task="code", success=True, quality=0.9
            )
        )
    report = store.leaderboard("code")
    assert report[0]["model"] == "local"
    assert report[0]["samples"] == 3
