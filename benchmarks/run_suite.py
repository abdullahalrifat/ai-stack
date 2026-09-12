"""Run the real-repository benchmark manifest against a configured Jarvis endpoint."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(Path(__file__).with_name("real-repository-suite.json")))
    parser.add_argument("--endpoint", required=True, help="Authenticated /runs endpoint")
    parser.add_argument("--output", default="benchmark-results.json")
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    results = []
    for case in manifest["tasks"]:
        payload = {"task": case["task"], "allow_write": True, "workspace": case.get("workspace", ".")}
        request = Request(args.endpoint, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json", **({"X-API-Key": args.api_key} if args.api_key else {})})
        started = time.monotonic()
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read())
        results.append({"id": case["id"], "seconds_to_submit": round(time.monotonic() - started, 3), "run": body, "verification": case["verification"]})
    output = {"schema_version": "1", "manifest": manifest["schema_version"], "results": results}
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
