# Certification evidence contract

A green unit-test run is necessary, not sufficient, for deployment certification. Retain a machine-readable evidence manifest for every release candidate and production rollout.

## Evidence manifest

Store one JSON artifact with at least:

- repository commit SHAs and release/package versions for Jarvis, Core, Inference and AI Stack;
- Python/runtime versions and the exact Compose image references/digests;
- inference model names and manifest digests;
- CI workflow/run URLs and conclusions;
- test and benchmark corpus revision, task count, pass/fail counts and incorrect-completion count;
- timeout/cancellation/duplicate-work conformance results;
- sandbox policy and prompt-injection/secret-canary results;
- soak start/end timestamps, duration, sample/probe counts, failure count and failure details;
- backup/restore result and isolated target identity (never credentials);
- target hardware CPU/RAM, resource budgets and free disk before/after testing.

Never include API keys, authorization headers, cookies, raw secrets or sensitive prompt payloads.

## Required release evidence

1. Run the server unit suite, Postgres lease/fencing tests, UI tests/build, Compose validation and deployment-safety check.
2. Run scripts/worker_soak.py against a disposable staging deployment for at least 24 hours before a production-readiness claim. This is a health/readiness soak only; worker lease, fencing and cancellation behavior must also pass dedicated chaos/integration tests.
3. Run scripts/backup_restore_dr_check.py only against an isolated disposable PostgreSQL restore target.
4. Capture inference latency/timeout behavior and verify an ambiguous timeout is not automatically replayed.
5. Confirm internal-only Qdrant/Pipelines ports are unpublished and external images are pinned to reviewed versions.
6. Link the retained artifact to the exact deployed commit and image/model manifest.

The control-plane soak workflow exercises a short fixture smoke on CI and performs the long staging health/readiness soak only when AI_STACK_SOAK_URL is configured as a repository variable. A fixture smoke is not a substitute for staging evidence.
