"""Write a commit-bound certification manifest without recording secrets."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

output = Path(sys.argv[1] if len(sys.argv) > 1 else "certification-evidence.json")
manifest = {
    "schema_version": 1,
    "repository": os.getenv("GITHUB_REPOSITORY", "local"),
    "commit_sha": os.getenv("GITHUB_SHA", "unknown"),
    "workflow": os.getenv("GITHUB_WORKFLOW", "local"),
    "workflow_run_id": os.getenv("GITHUB_RUN_ID", "unknown"),
    "workflow_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", "unknown"),
    "job_status": os.getenv("CI_JOB_STATUS", "unknown"),
    "recorded_at_utc": datetime.now(UTC).isoformat(),
    "deployment_gates": {
        "real_repository_benchmark": "pending_external_evidence",
        "prompt_injection_and_secret_canary": "pending_external_evidence",
        "hardware_soak_24h_72h": "pending_external_evidence",
        "backup_restore_drill": "pending_external_evidence",
        "sandbox_isolation_validation": "pending_external_evidence",
        "model_quality_and_latency_measurement": "pending_external_evidence",
    },
    "note": (
        "This manifest records CI provenance only. Pending deployment gates are not "
        "certified by a unit-test run. Never add credentials or raw secret payloads."
    ),
}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"Wrote commit-bound certification manifest: {output}")
