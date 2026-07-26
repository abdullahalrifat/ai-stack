"""Run fixed, human-readable agent regressions against a running stack.

Usage: python evals/run_evals.py --base http://127.0.0.1:8000 --key "$AGENT_API_KEY"
The script exits non-zero when a response misses a required phrase or contains
one of the known regression phrases. It intentionally reports results rather
than claiming to score model intelligence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    cases = json.loads((Path(__file__).parent / "cases.json").read_text())
    failures = []
    for case in cases:
        response = requests.post(
            f"{args.base.rstrip('/')}/chat",
            headers={"Authorization": f"Bearer {args.key}"},
            json={"message": case["prompt"], "model": case["profile"]},
            timeout=360,
        )
        answer = response.json().get("answer", "") if response.ok else response.text
        lower = answer.lower()
        missing = [value for value in case["must_contain"] if value.lower() not in lower]
        forbidden = [value for value in case["forbid"] if value.lower() in lower]
        status = "PASS" if response.ok and not missing and not forbidden else "FAIL"
        print(f"{status} {case['id']}")
        if status == "FAIL":
            failures.append({"id": case["id"], "http": response.status_code, "missing": missing, "forbidden": forbidden})
    if failures:
        print(json.dumps(failures, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
