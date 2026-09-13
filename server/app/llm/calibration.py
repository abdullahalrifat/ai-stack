"""Persistent, cost-aware model calibration from real runtime observations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from time import time
from typing import Iterable


@dataclass(frozen=True)
class RuntimeObservation:
    model: str
    task: str
    success: bool
    quality: float
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0
    tool_failures: int = 0
    source: str = "runtime"
    recorded_at: float = 0.0


class RuntimeCalibration:
    """Recency-weighted evidence used to tune automatic model selection.

    A route cannot become preferred from a single lucky observation. Quality and
    correctness dominate cost; cost is used to break ties among routes that meet
    the quality floor. Old evidence decays with a 30-day half-life.
    """

    def __init__(self, path: str | Path | None = None, *, min_samples: int = 3):
        configured = path or os.getenv(
            "JARVIS_RUNTIME_CALIBRATION_FILE",
            "/tmp/jarvis-runtime-calibration.jsonl",
        )
        self.path = Path(configured).expanduser()
        self.min_samples = max(1, int(min_samples))

    def record(self, observation: RuntimeObservation) -> None:
        row = observation
        if not row.recorded_at:
            row = RuntimeObservation(**{**asdict(row), "recorded_at": time()})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    def observations(self) -> list[RuntimeObservation]:
        if not self.path.is_file():
            return []
        result: list[RuntimeObservation] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return result
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                payload.setdefault("recorded_at", 0.0)
                payload.setdefault("source", "runtime")
                result.append(RuntimeObservation(**payload))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    @staticmethod
    def _weight(recorded_at: float, now: float) -> float:
        if not recorded_at:
            return 0.01
        age_days = max(0.0, now - recorded_at) / 86400.0
        return 0.5 ** (age_days / 30.0)

    def score(self, model: str, task: str, *, now: float | None = None) -> tuple[float, int] | None:
        now = time() if now is None else now
        rows = [
            row
            for row in self.observations()
            if row.model == model and row.task in {task, "general", "*"}
        ]
        if not rows:
            return None
        weighted = [(row, self._weight(row.recorded_at, now)) for row in rows]
        total = sum(weight for _, weight in weighted) or 1.0
        success = sum((1.0 if row.success else 0.0) * weight for row, weight in weighted) / total
        quality = sum(max(0.0, min(1.0, row.quality)) * weight for row, weight in weighted) / total
        failures = sum(max(0, row.tool_failures) * weight for row, weight in weighted) / total
        latency = sum(max(0.0, row.latency_ms) * weight for row, weight in weighted) / total
        cost = sum(max(0.0, row.cost_usd) * weight for row, weight in weighted) / total
        utility = (
            success * 0.50
            + quality * 0.35
            - min(latency / 10_000.0, 1.0) * 0.05
            - min(cost, 1.0) * 0.10
            - min(failures / 5.0, 1.0) * 0.15
        )
        return utility, len(rows)

    def select(
        self,
        models: Iterable[str],
        task: str,
        *,
        quality_floor: float = 0.70,
    ) -> str | None:
        """Return the best calibrated model, or None when evidence is insufficient."""
        candidates: list[tuple[float, int, float, str]] = []
        for model in models:
            rows = [
                row
                for row in self.observations()
                if row.model == model and row.task in {task, "general", "*"}
            ]
            if len(rows) < self.min_samples:
                continue
            now = time()
            weighted = [(row, self._weight(row.recorded_at, now)) for row in rows]
            total = sum(weight for _, weight in weighted) or 1.0
            quality = sum(max(0.0, min(1.0, row.quality)) * weight for row, weight in weighted) / total
            if quality < quality_floor:
                continue
            scored = self.score(model, task, now=now)
            if scored:
                candidates.append((scored[0], scored[1], -quality, model))
        if not candidates:
            return None
        return max(candidates)[-1]

    def leaderboard(self, task: str) -> list[dict[str, object]]:
        models = sorted({row.model for row in self.observations()})
        result = []
        for model in models:
            scored = self.score(model, task)
            if scored:
                result.append(
                    {"model": model, "utility": scored[0], "samples": scored[1]}
                )
        return sorted(result, key=lambda item: (item["utility"], item["samples"]), reverse=True)
