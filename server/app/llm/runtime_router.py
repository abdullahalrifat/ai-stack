"""Runtime route selection backed by empirical AI Stack observations."""

from __future__ import annotations

from .calibration import RuntimeCalibration


def select_calibrated_model(models: list[str], task: str) -> str | None:
    """Select a model only when real evidence clears the quality/sample floor."""
    return RuntimeCalibration().select(models, task, quality_floor=0.70)


def calibration_leaderboard(task: str) -> list[dict[str, object]]:
    """Return the current empirical route leaderboard for a workload class."""
    return RuntimeCalibration().leaderboard(task)
