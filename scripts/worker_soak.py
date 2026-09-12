"""Synthetic long-duration worker/control-plane soak harness.

It exercises the authenticated health/readiness endpoints and records failure
counts without creating real repository mutations. Run it against a disposable
or staging AI Stack instance.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--max-failures", type=int, default=3)
    args = parser.parse_args()
    deadline = time.monotonic() + args.hours * 3600
    failures = 0
    samples = 0
    while time.monotonic() < deadline:
        samples += 1
        try:
            with urllib.request.urlopen(args.url.rstrip("/") + "/health", timeout=10) as response:
                payload = json.loads(response.read().decode())
                if response.status != 200 or not payload.get("status"):
                    raise RuntimeError(f"unexpected health response: {payload}")
        except Exception as exc:
            failures += 1
            print(f"SOAK FAILURE {failures}: {exc}")
            if failures > args.max_failures:
                return 1
        time.sleep(max(1.0, args.interval))
    print(json.dumps({"samples": samples, "failures": failures, "hours": args.hours}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
