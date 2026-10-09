"""Bounded health/readiness soak harness with machine-readable evidence.

Use against a disposable/staging AI Stack instance. The CI workflow runs a
short integration smoke against a local fixture and the scheduled/manual path
can run a long soak against AI_STACK_SOAK_URL.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _probe(url: str, timeout: float) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        payload = json.loads(body)
        if response.status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"unexpected response from {url}: HTTP {response.status}")
        if url.endswith("/health") and not payload.get("status"):
            raise RuntimeError(f"health endpoint did not report status: {payload}")
        if url.endswith("/ready") and payload.get("status") not in ("ready", "ok"):
            raise RuntimeError(f"readiness endpoint is not ready: {payload}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--report", default="worker-soak-report.json")
    parser.add_argument("--readiness", action="store_true", help="also probe /ready")
    args = parser.parse_args()
    if args.hours <= 0 or args.interval <= 0 or args.timeout <= 0 or args.max_failures < 0:
        parser.error("hours, interval, and timeout must be positive; max-failures cannot be negative")

    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + args.hours * 3600
    failures: list[dict[str, str]] = []
    samples = 0
    endpoints = [args.url.rstrip("/") + "/health"]
    if args.readiness:
        endpoints.append(args.url.rstrip("/") + "/ready")
    while time.monotonic() < deadline:
        samples += 1
        for endpoint in endpoints:
            try:
                _probe(endpoint, args.timeout)
            except Exception as exc:
                failures.append({"endpoint": endpoint, "error": f"{type(exc).__name__}: {exc}"})
                print(f"SOAK FAILURE {len(failures)} endpoint={endpoint}: {exc}", flush=True)
                if len(failures) > args.max_failures:
                    deadline = 0
                    break
        if len(failures) > args.max_failures:
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(args.interval, remaining))

    report = {
        "schema_version": 1,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "target": args.url,
        "hours_requested": args.hours,
        "samples": samples,
        "probes_per_sample": len(endpoints),
        "failure_count": len(failures),
        "failures": failures[:100],
    }
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
