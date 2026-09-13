"""AI Stack adapter for the provider-neutral Core route calibrator."""

from __future__ import annotations

from pathlib import Path
from time import time

from jarvis_core import RouteCalibrator, RouteObservation


def _calibrator() -> RouteCalibrator:
    import os

    path = os.getenv(
        "JARVIS_ROUTE_CALIBRATION_FILE",
        "/tmp/jarvis-route-calibration.json",
    )
    return RouteCalibrator(
        Path(path),
        min_samples=int(os.getenv("JARVIS_ROUTE_CALIBRATION_MIN_SAMPLES", "3")),
        quality_floor=float(os.getenv("JARVIS_ROUTE_CALIBRATION_QUALITY_FLOOR", "0.70")),
    )


def record_runtime_observation(
    *,
    route: str,
    category: str,
    success: bool,
    quality: float,
    latency_ms: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float = 0.0,
    tool_failures: int = 0,
    incorrect_completion: bool = False,
    cached_input_tokens: int = 0,
    source: str = "runtime",
) -> None:
    """Persist runtime telemetry as a Core RouteObservation."""
    _calibrator().record(
        RouteObservation(
            route=route,
            category=category,
            success=success,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            tool_failures=tool_failures,
            incorrect_completion=incorrect_completion,
            quality=quality,
            cached_input_tokens=cached_input_tokens,
            source=source,
            recorded_at=time(),
        )
    )


def select_empirical_route(
    routes: list[str], category: str, *, fallback: str
) -> str:
    """Use Core calibration only when evidence clears its safeguards."""
    return _calibrator().select(routes, category, fallback=fallback)
