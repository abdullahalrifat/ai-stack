"""Small application-facing API for empirical route calibration."""

from __future__ import annotations

from .calibration import RuntimeCalibration


def calibrated_model(models: list[str], task: str) -> str | None:
    return RuntimeCalibration().select(models, task, quality_floor=0.70)


def calibration_report(task: str) -> list[dict[str, object]]:
    return RuntimeCalibration().leaderboard(task)
